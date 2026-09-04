"""Finite request-plan execution and streaming orchestration for AVA."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from dataclasses import replace
import inspect
import json
import logging
import os
import random
import re
from pathlib import Path
import resource
import time
from typing import Any
from uuid import uuid4

import numpy as np
from sentence_transformers import SentenceTransformer

from src.config.settings import (
    ALLOWED_MODELS,
    PipelineSettings,
    ProviderSettings,
)
from src.embeddings.embed_chunks import MODEL_CONFIGS
from src.backend.dependencies import (
    FILINGS,
    PROJECT_ROOT,
    build_bm25_index,
    corpus_version,
    load_corpus,
)
from src.filings.corpus import COMPANY_NAMES
from src.generation.rag import (
    CitationVisibilityFilter,
    GenerationResult,
    GenerationService,
    ProviderCircuitBreaker,
    count_generation_input_tokens,
    make_llm_client,
    resolve_cited_evidence,
    visible_answer_text,
)
from src.indexing.qdrant_index import (
    alias_target,
    make_client,
)
from src.observability import RequestTrace, safe_error_class
from src.orchestration.routing import (
    RequestRoute,
    RouteKind,
    RouteReason,
    deterministic_route,
    explicit_filing_source_requested,
)
from src.resolution.companies import (
    CompanyResolver,
    confidence_band,
    default_company_resolver,
)
from src.retrieval.scope_aware import ScopeAwareRetriever
from src.retrieval.dense import (
    LocalArtifactRetriever,
    QdrantRetriever,
    ShadowDenseRetriever,
)
from src.retrieval.evidence_policy import (
    EvidenceBudgetPolicy,
    EvidencePackingError,
    EvidencePolicyError,
)
from src.tools import (
    DEFAULT_ALLOWED_DOMAINS,
    BraveWebSearchTool,
    CalculationError,
    CalculatorTool,
    UnavailableWebSearchTool,
    WebSearchTool,
    infer_calculation_operation,
)

from src.backend.sources import normalize_sources
from src.orchestration.handlers import PipelineEvent, RouteHandlerMixin, activity_event


AVAILABLE_MODELS = list(ALLOWED_MODELS)
LOGGER = logging.getLogger(__name__)
TelemetrySink = Callable[[dict[str, Any]], None]
GENERATION_ACTIVITIES = (
    "Thinking", "Reasoning", "Cogitating", "Cerebrating", "Contemplating",
    "Pondering", "Ruminating", "Sleuthing",
)
UPLOAD_MATCH_STOP_WORDS = frozenset(
    {
        "about", "after", "also", "answer", "attached", "before", "build",
        "built", "company", "could", "document", "does", "file", "from",
        "have", "information", "into", "latest", "many", "much", "question",
        "report", "source", "that", "their", "these", "they", "this", "those",
        "uploaded", "uses", "using", "what", "when", "where", "which", "with",
        "would",
    }
)



def infer_filing_scope_query(query: str) -> bool:
    """Recognize concise filing facts that can use a selected company scope."""
    return bool(re.search(
        r"\b(?:ceos?|coos?|cfos?|ctos?|chief\s+(?:executive|operating|financial|technology)\s+officer|"
        r"executive|officer|leadership|product|technology|business|strategy|risk|revenue|"
        r"sales|income|profit|financial|employees?|autonomous|adas|evs?)\b",
        query,
        re.IGNORECASE,
    ))


def uploaded_evidence_matches_query(query: str, results: Sequence[Any]) -> bool:
    """Require a strong lexical bridge before an upload can pre-empt filing RAG."""
    query_terms = {
        term
        for term in re.findall(r"[a-z0-9]+", query.casefold())
        if len(term) >= 4 and term not in UPLOAD_MATCH_STOP_WORDS
    }
    if not query_terms:
        return False
    for result in results:
        searchable = f"{getattr(result, 'filename', '')} {getattr(result, 'text', '')}"
        evidence_terms = set(re.findall(r"[a-z0-9]+", searchable.casefold()))
        overlap = query_terms & evidence_terms
        if len(overlap) >= 2 or any(len(term) >= 8 for term in overlap):
            return True
    return False


def without_calculator_route(route: RequestRoute) -> RequestRoute:
    """Remove calculator execution from a route while preserving useful evidence."""
    replacements = {
        RouteKind.CALCULATOR: (
            RouteKind.CONVERSATION_ONLY,
            RouteReason.OUT_OF_SCOPE,
        ),
        RouteKind.FILING_AND_CALCULATOR: (
            RouteKind.FILING_RAG,
            RouteReason.FILING_EVIDENCE,
        ),
        RouteKind.WEB_AND_CALCULATOR: (
            RouteKind.WEB_SEARCH,
            RouteReason.CURRENT_OR_EXTERNAL,
        ),
        RouteKind.UPLOAD_AND_CALCULATOR: (
            RouteKind.UPLOADED_DOCUMENT_RAG,
            RouteReason.UPLOADED_EVIDENCE,
        ),
    }
    replacement = replacements.get(route.route)
    if replacement is None:
        return route
    route_kind, reason = replacement
    return RequestRoute(
        route_kind,
        reason,
        arithmetic_required=False,
        decided_by=f"{route.decided_by}_calculator_disabled",
    )


def ava_introduction() -> str:
    """Return the deterministic, corpus-aware AVA introduction."""
    available = ", ".join(
        f"{COMPANY_NAMES[ticker]} ({ticker})" for ticker in FILINGS
    )
    return (
        "Hello! I'm AVA, your Autonomous Vehicle Analyst. I search the indexed "
        "annual SEC filings, explain company disclosures, compare companies, and "
        "cite the evidence I use.\n\n"
        f"Available companies: {available}.\n\n"
        "For example, try:\n"
        "- What was General Motors' total consolidated revenue?\n"
        "- What future plans does Aurora disclose?\n"
        "- Who was Tesla's CEO as of its latest indexed 10-K?"
    )


def company_scope_mismatch_message(
    requested_tickers: Sequence[str], selected_tickers: Sequence[str]
) -> str:
    """Explain an explicit query target excluded by the chat's saved scope."""
    requested = ", ".join(
        f"{COMPANY_NAMES[ticker]} ({ticker})" for ticker in requested_tickers
    )
    selected = ", ".join(
        f"{COMPANY_NAMES[ticker]} ({ticker})" for ticker in selected_tickers
    )
    return (
        f"Your question targets {requested}, but this chat's company scope is "
        f"limited to {selected}. In the sidebar, add the requested company or "
        "select All companies, then ask again."
    )



class RealPipeline(RouteHandlerMixin):
    mode = "real"

    def __init__(
        self,
        retriever: ScopeAwareRetriever,
        generator: GenerationService,
        *,
        llm_streaming: bool = True,
        company_resolver: CompanyResolver = default_company_resolver,
        corpus_version_value: str = "unknown",
        index_version: str = "local-npz-bm25",
        telemetry_sink: TelemetrySink | None = None,
        startup_metrics: dict[str, Any] | None = None,
        ready: bool = True,
        qdrant_health: dict[str, Any] | None = None,
        request_routing_enabled: bool = True,
        calculator: CalculatorTool | None = None,
        calculator_enabled: bool = False,
        web_search: WebSearchTool | None = None,
        web_search_enabled: bool = False,
        web_search_max_results: int = 5,
        max_tool_executions: int = 4,
        max_web_searches: int = 2,
        emit_activity: bool = False,
    ) -> None:
        self.retriever = retriever
        self.generator = generator
        self.llm_streaming = llm_streaming
        self.answer_delivery = "provider_streaming" if llm_streaming else "buffered"
        self.company_resolver = company_resolver
        self.corpus_version = corpus_version_value
        self.index_version = index_version
        self.telemetry_sink = telemetry_sink or self._log_trace
        self.startup_metrics = startup_metrics or {}
        self.ready = ready
        self.qdrant_health = qdrant_health or {
            "configured": False,
            "mode": "disabled",
            "status": "disabled",
        }
        self.request_routing_enabled = request_routing_enabled
        self.calculator = calculator or CalculatorTool()
        self.calculator_enabled = calculator_enabled
        self.web_search = web_search or UnavailableWebSearchTool()
        self.web_search_enabled = web_search_enabled
        self.web_search_max_results = web_search_max_results
        if max_tool_executions <= 0 or max_web_searches <= 0:
            raise ValueError("AVA tool execution limits must be positive.")
        if max_web_searches > max_tool_executions:
            raise ValueError("Maximum web searches cannot exceed total tool executions.")
        self.max_tool_executions = max_tool_executions
        self.max_web_searches = max_web_searches
        self.emit_activity = emit_activity

    @staticmethod
    def _log_trace(record: dict[str, Any]) -> None:
        LOGGER.info("AVA request completed", extra={"ava_request": record})


    def close(self) -> None:
        close_provider = getattr(self.generator.client, "close", None)
        if callable(close_provider):
            close_provider()
        self.web_search.close()
        dense = self.retriever.dense_retriever
        if isinstance(dense, ShadowDenseRetriever):
            dense = dense.shadow
        client = getattr(dense, "client", None)
        close_qdrant = getattr(client, "close", None)
        if callable(close_qdrant):
            close_qdrant()

    @classmethod
    def build(
        cls,
        settings: PipelineSettings,
        provider_settings: ProviderSettings | None = None,
    ) -> "RealPipeline":
        startup_started = time.perf_counter()
        load_started = time.perf_counter()
        embeddings, chunks = load_corpus()
        load_ms = (time.perf_counter() - load_started) * 1_000
        normalized = embeddings / np.clip(
            np.linalg.norm(embeddings, axis=1, keepdims=True), 1e-12, None
        )
        bm25_started = time.perf_counter()
        bm25_retriever = build_bm25_index(chunks)
        bm25_ms = (time.perf_counter() - bm25_started) * 1_000
        embedding_config = MODEL_CONFIGS["bgebase"]
        model_started = time.perf_counter()
        embedder = SentenceTransformer(
            embedding_config["repository"],
            revision=embedding_config["revision"],
            device=settings.model_device,
        )
        model_ms = (time.perf_counter() - model_started) * 1_000
        corpus_id = corpus_version(chunks)
        evidence_policy = EvidenceBudgetPolicy(
            context_window_tokens=settings.context_window_tokens,
            reserved_output_tokens=settings.reserved_output_tokens,
        )
        local_dense = LocalArtifactRetriever(
            model=embedder,
            query_prefix=embedding_config["query_prefix"],
            normalized_embeddings=normalized,
            all_chunks=chunks,
        )
        dense_retriever = local_dense
        qdrant_health: dict[str, Any] = {
            "configured": settings.qdrant_mode != "disabled",
            "mode": settings.qdrant_mode,
            "status": "disabled",
        }
        ready = True
        qdrant_target: str | None = None
        if settings.qdrant_mode != "disabled":
            try:
                local_path = (
                    Path(settings.qdrant_local_path).expanduser().resolve()
                    if settings.qdrant_local_path
                    else None
                )
                qdrant_client = make_client(
                    url=None if local_path else settings.qdrant_url,
                    api_key=settings.qdrant_api_key,
                    local_path=local_path,
                    timeout=settings.qdrant_timeout_seconds,
                )
                qdrant_target = alias_target(
                    qdrant_client, settings.qdrant_collection_alias
                )
                if qdrant_target is None:
                    raise RuntimeError("Configured Qdrant read alias does not exist.")
                qdrant_dense = QdrantRetriever(
                    client=qdrant_client,
                    collection_name=settings.qdrant_collection_alias,
                    model=embedder,
                    query_prefix=embedding_config["query_prefix"],
                    all_chunks=chunks,
                )
                qdrant_health = {
                    "configured": True,
                    "mode": settings.qdrant_mode,
                    "alias": settings.qdrant_collection_alias,
                    "alias_target": qdrant_target,
                    **qdrant_dense.health_check(),
                }
                if qdrant_health["point_count"] != len(chunks):
                    raise RuntimeError("Qdrant point count does not match the corpus.")
                dense_retriever = (
                    ShadowDenseRetriever(primary=local_dense, shadow=qdrant_dense)
                    if settings.qdrant_mode == "shadow"
                    else qdrant_dense
                )
            except Exception as error:
                ready = False
                qdrant_health = {
                    "configured": True,
                    "mode": settings.qdrant_mode,
                    "status": "unavailable",
                    "alias": settings.qdrant_collection_alias,
                    "safe_error_class": safe_error_class(error),
                }
                LOGGER.exception("Configured Qdrant is unavailable; AVA is not ready")
        retriever = ScopeAwareRetriever(
            model=embedder,
            query_prefix=embedding_config["query_prefix"],
            normalized_embeddings=normalized,
            bm25_retriever=bm25_retriever,
            all_chunks=chunks,
            evidence_policy=evidence_policy,
            token_counter=count_generation_input_tokens,
            dense_retriever=dense_retriever,
        )
        provider_settings = provider_settings or ProviderSettings.from_environment()
        generator = GenerationService(
            make_llm_client(provider_settings),
            model=settings.llm_model,
            max_output_tokens=settings.reserved_output_tokens,
            circuit_breaker=ProviderCircuitBreaker(
                failure_threshold=provider_settings.circuit_failures,
                recovery_seconds=provider_settings.circuit_recovery_seconds,
            ),
        )
        web_search: WebSearchTool = UnavailableWebSearchTool()
        if settings.web_search_enabled and settings.web_search_provider == "brave":
            web_search = BraveWebSearchTool(
                settings.web_search_api_key or "",
                timeout_seconds=settings.web_search_timeout_seconds,
                allowed_domains=DEFAULT_ALLOWED_DOMAINS,
            )
        startup_metrics = {
            "corpus_load_ms": round(load_ms, 3),
            "bm25_build_ms": round(bm25_ms, 3),
            "embedding_model_load_ms": round(model_ms, 3),
            "complete_ms": round((time.perf_counter() - startup_started) * 1_000, 3),
            "resident_memory_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3),
            "cpu_count": os.cpu_count(),
            "chunk_count": len(chunks),
            "corpus_version": corpus_id,
            "index_version": (
                f"qdrant-{settings.qdrant_mode}:{qdrant_target}+bm25:{corpus_id}"
                if qdrant_target
                else f"local-npz-bm25:{corpus_id}"
            ),
            "dense_backend": dense_retriever.identity,
            "qdrant": qdrant_health,
            "observability_retention_days": settings.observability_retention_days,
            "filing_prompt_version": generator.prompt_version,
        }
        LOGGER.info("AVA pipeline ready", extra={"ava_startup": startup_metrics})
        return cls(
            retriever,
            generator,
            llm_streaming=settings.llm_streaming,
            company_resolver=default_company_resolver,
            corpus_version_value=corpus_id,
            index_version=startup_metrics["index_version"],
            startup_metrics=startup_metrics,
            ready=ready,
            qdrant_health=qdrant_health,
            request_routing_enabled=settings.request_routing_enabled,
            calculator_enabled=settings.calculator_enabled,
            web_search=web_search,
            web_search_enabled=settings.web_search_enabled,
            web_search_max_results=settings.web_search_max_results,
            max_tool_executions=settings.max_tool_executions,
            max_web_searches=settings.max_web_searches,
            emit_activity=True,
        )

    async def stream(
        self,
        query: str,
        is_disconnected: Callable[[], Awaitable[bool]],
        request_id: str | None = None,
        conversation_context: Any | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        document_service: Any | None = None,
        company_scope: list[str] | tuple[str, ...] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[PipelineEvent]:
        if model is not None and model not in ALLOWED_MODELS:
            raise ValueError("That model is not available.")
        request_generator = (
            self.generator.for_model(model)
            if model is not None
            else self.generator
        )
        trace = RequestTrace(
            original_query=query,
            request_id=request_id or str(uuid4()),
            corpus_version=self.corpus_version,
            index_version=self.index_version,
            answer_delivery=self.answer_delivery,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        try:
            async for event in self._stream_traced(
                query, is_disconnected, trace, conversation_context, document_service,
                company_scope, request_generator,
            ):
                yield event
        except asyncio.CancelledError:
            trace.cancelled = True
            raise
        except Exception as error:
            trace.safe_error_class = safe_error_class(error)
            raise
        finally:
            try:
                self.telemetry_sink(trace.as_record())
            except Exception:
                LOGGER.exception("AVA telemetry sink failed")

    async def _stream_traced(
        self,
        query: str,
        is_disconnected: Callable[[], Awaitable[bool]],
        trace: RequestTrace,
        conversation_context: Any | None = None,
        document_service: Any | None = None,
        company_scope: list[str] | tuple[str, ...] | None = None,
        generator: Any | None = None,
    ) -> AsyncIterator[PipelineEvent]:
        async def disconnected() -> bool:
            value = await is_disconnected()
            if value:
                trace.cancelled = True
            return value

        generator = generator or self.generator
        prompt_context = (
            conversation_context.prompt_text()
            if conversation_context is not None
            else ""
        )
        if conversation_context is not None:
            trace.short_term_memory_ids = list(conversation_context.short_term_ids)
            trace.long_term_memory_ids = list(conversation_context.long_term_ids)
        uploaded_documents = []
        if (
            self.request_routing_enabled
            and document_service is not None
            and trace.conversation_id is not None
        ):
            with trace.stage("uploaded_document_listing"):
                uploaded_documents = await asyncio.to_thread(
                    document_service.list, trace.conversation_id
                )
        uploaded_source_names = [item.filename for item in uploaded_documents]
        with trace.stage("deterministic_resolution"):
            deterministic_resolution = self.company_resolver.resolve(query)
        preliminary_route = (
            deterministic_route(
                query,
                deterministic_resolution,
                uploads_available=bool(uploaded_source_names),
                conversation_context=prompt_context,
            )
            if self.request_routing_enabled
            else None
        )
        upload_candidates: list[Any] = []
        upload_match = False
        should_search_uploads = bool(
            uploaded_documents
            and (
                preliminary_route is None
                or preliminary_route.route
                not in {RouteKind.CONVERSATION_ONLY, RouteKind.CALCULATOR}
            )
        )
        if should_search_uploads:
            if self.emit_activity:
                yield activity_event("Searching through uploaded documents")
            with trace.stage("uploaded_document_search"):
                upload_candidates = await asyncio.to_thread(
                    document_service.search,
                    trace.conversation_id,
                    query,
                    limit=10,
                )
            upload_match = bool(
                not explicit_filing_source_requested(query)
                and uploaded_evidence_matches_query(query, upload_candidates)
            )
        selected_scope = tuple(company_scope or ())
        excluded_query_tickers = tuple(
            ticker
            for ticker in deterministic_resolution.resolved_tickers
            if selected_scope and ticker not in selected_scope
        )
        if excluded_query_tickers and not upload_match:
            route = RequestRoute(
                RouteKind.CLARIFY,
                RouteReason.AMBIGUOUS_INTENT,
                decided_by="manual_company_scope_mismatch",
            )
            trace.route = route.as_dict()
            trace.resolver = {
                "resolved_tickers": list(deterministic_resolution.resolved_tickers),
                "selected_tickers": list(selected_scope),
                "excluded_query_tickers": list(excluded_query_tickers),
            }
            response_text = company_scope_mismatch_message(
                excluded_query_tickers, selected_scope
            )
            trace.generated_answer = response_text
            trace.source_status = "none_cited"
            trace.mark_first_token()
            yield PipelineEvent("delta", {"text": response_text})
            yield PipelineEvent(
                "sources",
                {
                    "sources": [],
                    "source_status": "none_cited",
                    "malformed_source_count": 0,
                },
            )
            yield PipelineEvent("done", {})
            return
        with trace.stage("routing"):
            if upload_match:
                route = RequestRoute(
                    RouteKind.UPLOADED_DOCUMENT_RAG,
                    RouteReason.UPLOADED_EVIDENCE,
                    decided_by="chat_upload_content_match",
                )
            elif selected_scope and infer_filing_scope_query(query):
                route = RequestRoute(
                    RouteKind.FILING_RAG,
                    RouteReason.FILING_EVIDENCE,
                    decided_by="manual_company_scope",
                )
            elif not self.request_routing_enabled:
                route = RequestRoute(
                    RouteKind.FILING_RAG,
                    RouteReason.FILING_EVIDENCE,
                    decided_by="filing_only_kill_switch",
                )
            elif hasattr(generator, "route_request"):
                if prompt_context or uploaded_source_names:
                    route = await asyncio.to_thread(
                        generator.route_request,
                        query,
                        deterministic_resolution,
                        prompt_context,
                        uploaded_source_names,
                    )
                else:
                    route = await asyncio.to_thread(
                        generator.route_request,
                        query,
                        deterministic_resolution,
                    )
            else:
                # Compatibility for narrow evaluator/test generators. Production
                # GenerationService always owns the typed router.
                route = RequestRoute(
                    RouteKind.FILING_RAG,
                    RouteReason.FILING_EVIDENCE,
                    decided_by="compatibility_fallback",
                )
        if not self.calculator_enabled:
            route = without_calculator_route(route)
        trace.route = route.as_dict()
        if route.uses_web_search and not self.web_search_enabled:
            # Keep the non-filing boundary intact. The web handler emits a safe,
            # source-free unavailable response without invoking filing planning.
            LOGGER.info("AVA web search required but disabled")
        LOGGER.info("AVA request route", extra={"ava_request_route": trace.route})

        required_web_searches = 1 if route.uses_web_search else 0
        required_tool_executions = required_web_searches + (
            1 if route.uses_calculator else 0
        )
        if (
            required_tool_executions > self.max_tool_executions
            or required_web_searches > self.max_web_searches
        ):
            response_text = (
                "That request exceeds this deployment's bounded tool-execution "
                "limit, so no tools were run."
            )
            trace.generated_answer = response_text
            trace.source_status = "none_cited"
            trace.mark_first_token()
            yield PipelineEvent("delta", {"text": response_text})
            yield PipelineEvent(
                "sources",
                {
                    "sources": [],
                    "source_status": "none_cited",
                    "malformed_source_count": 0,
                },
            )
            yield PipelineEvent("done", {})
            return

        if route.route is RouteKind.CALCULATOR:
            calculation = None
            if not self.calculator_enabled:
                response_text = (
                    "That request requires the calculator, but it is disabled in this "
                    "deployment. I won't guess or calculate it in the language model."
                )
            else:
                try:
                    with trace.stage("calculator"):
                        calculation = self.calculator.calculate_query(query)
                    trace.tool_executions.append(
                        {"tool": "calculator", "status": "succeeded", **calculation.as_dict()}
                    )
                    response_text = calculation.render()
                except CalculationError:
                    trace.tool_executions.append(
                        {
                            "tool": "calculator",
                            "status": "rejected",
                            "safe_error_class": "invalid_calculation",
                        }
                    )
                    response_text = (
                        "I couldn't safely parse that calculation. Please provide the "
                        "numeric operands and operation explicitly."
                    )
            if calculation is not None:
                if self.emit_activity:
                    yield activity_event(f"Calculating {calculation.normalized_expression}")
            trace.generated_answer = response_text
            trace.source_status = "none_cited"
            trace.mark_first_token()
            yield PipelineEvent("delta", {"text": response_text})
            yield PipelineEvent(
                "sources",
                {
                    "sources": [],
                    "source_status": "none_cited",
                    "malformed_source_count": 0,
                },
            )
            yield PipelineEvent("done", {})
            return

        if route.uses_calculator and not self.calculator_enabled:
            response_text = (
                "That request requires the calculator, but it is disabled in this "
                "deployment. I won't guess or calculate it in the language model."
            )
            trace.generated_answer = response_text
            trace.source_status = "none_cited"
            trace.mark_first_token()
            yield PipelineEvent("delta", {"text": response_text})
            yield PipelineEvent(
                "sources",
                {
                    "sources": [],
                    "source_status": "none_cited",
                    "malformed_source_count": 0,
                },
            )
            yield PipelineEvent("done", {})
            return

        if route.uses_web_search:
            async for event in self._stream_web_route(
                query, route, disconnected, trace, generator
            ):
                yield event
            return

        if route.uses_uploads:
            if document_service is None or trace.conversation_id is None:
                response_text = (
                    "That question needs a document attached to this chat, but no "
                    "usable chat document source is available."
                )
                trace.generated_answer = response_text
                trace.source_status = "none_cited"
                trace.mark_first_token()
                yield PipelineEvent("delta", {"text": response_text})
                yield PipelineEvent(
                    "sources",
                    {
                        "sources": [],
                        "source_status": "none_cited",
                        "malformed_source_count": 0,
                    },
                )
                yield PipelineEvent("done", {})
                return
            async for event in self._stream_upload_route(
                query,
                route,
                disconnected,
                trace,
                document_service,
                generator,
                upload_candidates if upload_candidates else None,
            ):
                yield event
            return

        if not route.uses_filing_retrieval:
            if route.reason_code is RouteReason.GREETING:
                response_text = ava_introduction()
            elif route.reason_code is RouteReason.AVA_HELP:
                response_text = ava_introduction()
            elif route.reason_code is RouteReason.OUT_OF_SCOPE:
                response_text = (
                    "That request is outside AVA's SEC-filing analysis scope. I can "
                    "identify and cite disclosed executives or analyze company filing "
                    "evidence, but I don't provide programming exercises or arbitrary "
                    "letter-processing tasks."
                )
            elif route.route is RouteKind.CLARIFY:
                response_text = (
                    "I need a little more context to choose the right evidence. "
                    "Please name the company, source, or document you mean."
                )
            elif route.uses_web_search:
                response_text = (
                    "That question needs information outside the available SEC filings. "
                    "Web search is not enabled in this deployment yet."
                )
            elif route.uses_uploads:
                response_text = (
                    "That question needs a document attached to this chat, but no usable "
                    "chat document source is available yet."
                )
            else:
                response_text = (
                    "I can help with the available SEC filings. Ask about a company, "
                    "filing topic, or AVA's supported capabilities."
                )
            trace.generated_answer = response_text
            trace.source_status = "none_cited"
            trace.mark_first_token()
            yield PipelineEvent("delta", {"text": response_text})
            yield PipelineEvent(
                "sources",
                {
                    "sources": [],
                    "source_status": "none_cited",
                    "malformed_source_count": 0,
                },
            )
            yield PipelineEvent("done", {})
            return
        with trace.stage("planning"):
            planner_supports_scope = "selected_tickers" in inspect.signature(
                generator.plan_retrieval
            ).parameters
            planner_scope_kwargs = (
                {"selected_tickers": selected_scope}
                if selected_scope and planner_supports_scope
                else {}
            )
            if prompt_context:
                plan = await asyncio.to_thread(
                    generator.plan_retrieval,
                    query,
                    deterministic_resolution,
                    prompt_context,
                    **planner_scope_kwargs,
                )
            else:
                plan = await asyncio.to_thread(
                    generator.plan_retrieval,
                    query,
                    deterministic_resolution,
                    **planner_scope_kwargs,
                )
        # A manually selected scope is authoritative for this conversation.
        # Keep planner intent, but force every retrieval subquery to the
        # validated selected tickers; an empty selection means the full corpus.
        selected_scope = tuple(company_scope or ())
        if selected_scope:
            plan["resolved_tickers"] = list(selected_scope)
            plan["company_mentions"] = []
            plan["subqueries"] = [
                {"query": item["query"], "tickers": list(selected_scope)}
                for item in plan["subqueries"]
            ]
        elif deterministic_resolution.explicit_scope_tickers and re.search(
            r"\b(?:all|each|every)\s+(?:of\s+the\s+)?(?:eleven\s+)?companies\b",
            query,
            re.IGNORECASE,
        ):
            # “all companies” is a deterministic corpus scope. Do not let a
            # conservative planner reinterpret the word “all” as ambiguity.
            corpus_scope = tuple(deterministic_resolution.explicit_scope_tickers)
            plan["resolved_tickers"] = list(corpus_scope)
            plan["company_mentions"] = []
            plan["ambiguity"] = False
            plan["subqueries"] = [
                {"query": item["query"], "tickers": list(corpus_scope)}
                for item in plan["subqueries"]
            ]
        with trace.stage("validated_resolution"):
            resolution = self.company_resolver.apply_planner_resolution(
                deterministic_resolution,
                plan["company_mentions"],
                plan["resolved_tickers"],
            )
        # The LLM planner owns semantic intent. Deterministic resolution only
        # guards the allowed company set and ambiguity boundary; requesting
        # several companies does not automatically make a query comparative.
        resolution = replace(resolution, comparison=plan["comparison"])
        if route.uses_calculator and plan["operation"] is None:
            response_text = (
                "I couldn't identify a supported calculation operation from that request. "
                "Please specify a difference, ratio, percentage, growth rate, or sum."
            )
            trace.generated_answer = response_text
            trace.source_status = "none_cited"
            trace.mark_first_token()
            yield PipelineEvent("delta", {"text": response_text})
            yield PipelineEvent(
                "sources",
                {
                    "sources": [],
                    "source_status": "none_cited",
                    "malformed_source_count": 0,
                },
            )
            yield PipelineEvent("done", {})
            return
        if plan["ambiguity"] != resolution.needs_clarification:
            # The validated resolver is authoritative for the clarification
            # boundary.  LLM planners can conservatively mark a global or
            # enumeration query as ambiguous even when no company mention is
            # unresolved (for example, "what companies are developing ...").
            # Do not turn that harmless planner disagreement into a failed
            # request; retain it in diagnostics and continue with the
            # validated decision.
            LOGGER.warning(
                "Planner ambiguity disagrees with validated resolution; using validated decision",
                extra={
                    "ava_planner_ambiguity": plan["ambiguity"],
                    "ava_validated_ambiguity": resolution.needs_clarification,
                },
            )
            plan.setdefault("_normalizations", []).append(
                "planner_ambiguity_overridden_by_validated_resolution"
            )

        LOGGER.info(
            "AVA company resolution",
            extra={
                "ava_company_resolution": {
                    "resolved_tickers": list(resolution.resolved_tickers),
                    "explicit_scope_tickers": list(resolution.explicit_scope_tickers),
                    "planner_scope_tickers": list(resolution.planner_scope_tickers),
                    "mentions": [
                        {
                            "raw_text": mention.raw_text,
                            "ticker": mention.ticker,
                            "method": mention.method,
                            "confidence_band": confidence_band(mention.confidence),
                        }
                        for mention in resolution.mentions
                    ],
                    "unresolved_mentions": [
                        mention.raw_text for mention in resolution.unresolved_mentions
                    ],
                    "scope": resolution.scope,
                    "comparison": resolution.comparison,
                    "needs_clarification": resolution.needs_clarification,
                }
            },
        )
        trace.resolver = {
            "resolved_tickers": list(resolution.resolved_tickers),
            "explicit_scope_tickers": list(resolution.explicit_scope_tickers),
            "planner_scope_tickers": list(resolution.planner_scope_tickers),
            "mentions": [
                {
                    "raw_text": mention.raw_text,
                    "ticker": mention.ticker,
                    "method": mention.method,
                    "confidence_band": confidence_band(mention.confidence),
                }
                for mention in resolution.mentions
            ],
            "unresolved_mentions": [
                mention.raw_text for mention in resolution.unresolved_mentions
            ],
            "scope": resolution.scope,
            "comparison": resolution.comparison,
            "needs_clarification": resolution.needs_clarification,
            "planner_normalizations": list(plan.get("_normalizations", [])),
        }

        if resolution.needs_clarification:
            # An unresolved product/company is the one case where a bounded
            # external lookup is useful. Keep filing-first behavior for every
            # resolved request; only fall back when the planner cannot map the
            # target and web search is explicitly enabled.
            if self.web_search_enabled:
                fallback_route = RequestRoute(
                    RouteKind.WEB_SEARCH,
                    RouteReason.CURRENT_OR_EXTERNAL,
                    decided_by="unresolved_company_fallback",
                )
                async for event in self._stream_web_route(
                    query, fallback_route, disconnected, trace, generator
                ):
                    yield event
                return
            trace.generated_answer = self.company_resolver.clarification_message(resolution)
            trace.source_status = "none_cited"
            trace.mark_first_token()
            yield PipelineEvent(
                "delta",
                {"text": trace.generated_answer},
            )
            yield PipelineEvent(
                "sources",
                {
                    "sources": [],
                    "source_status": "none_cited",
                    "malformed_source_count": 0,
                },
            )
            yield PipelineEvent("done", {})
            return

        resolved_tickers = set(resolution.resolved_tickers)
        targeted_tickers = {
            ticker for item in plan["subqueries"] for ticker in item["tickers"]
        }
        if resolved_tickers and not resolved_tickers <= targeted_tickers:
            raise ValueError("Planner subqueries omitted a resolved company target.")
        if not targeted_tickers <= resolved_tickers:
            raise ValueError("Planner subqueries contain an unvalidated company target.")
        retrieval_queries = [
            self.company_resolver.retrieval_query(item["query"], item["tickers"])
            for item in plan["subqueries"]
        ]
        trace.retrieval_subqueries = retrieval_queries
        for ticker in dict.fromkeys(
            ticker for item in plan["subqueries"] for ticker in item["tickers"]
        ):
            if self.emit_activity:
                yield activity_event(f"Searching through {ticker} filings")
        try:
            with trace.stage("retrieval_selection"):
                retrieval_arguments = (
                    query,
                    retrieval_queries,
                    resolution,
                    [item["tickers"] for item in plan["subqueries"]],
                )
                if prompt_context:
                    outcome = await asyncio.to_thread(
                        self.retriever.retrieve,
                        *retrieval_arguments,
                        conversation_context=prompt_context,
                    )
                else:
                    outcome = await asyncio.to_thread(
                        self.retriever.retrieve, *retrieval_arguments
                    )
        except EvidencePolicyError as error:
            trace.safe_error_class = type(error).__name__
            trace.source_status = "none_cited"
            trace.generated_answer = (
                "AVA could not apply the configured filing-evidence policy. "
                "Please try again or contact the service operator."
            )
            trace.mark_first_token()
            LOGGER.warning("AVA evidence policy could not satisfy request: %s", error)
            yield PipelineEvent(
                "delta",
                {
                    "text": trace.generated_answer
                },
            )
            yield PipelineEvent(
                "sources",
                {
                    "sources": [],
                    "source_status": "none_cited",
                    "malformed_source_count": 0,
                },
            )
            yield PipelineEvent("done", {})
            return
        except EvidencePackingError as error:
            trace.safe_error_class = type(error).__name__
            trace.source_status = "none_cited"
            trace.generated_answer = (
                "AVA could not fit complete filing evidence for that request within "
                "the configured model budget. Please narrow the question."
            )
            trace.mark_first_token()
            LOGGER.warning("AVA evidence packing could not satisfy request: %s", error)
            yield PipelineEvent(
                "delta",
                {
                    "text": trace.generated_answer
                },
            )
            yield PipelineEvent(
                "sources",
                {
                    "sources": [],
                    "source_status": "none_cited",
                    "malformed_source_count": 0,
                },
            )
            yield PipelineEvent("done", {})
            return
        LOGGER.info(
            "AVA evidence selection",
            extra={
                "ava_evidence_selection": {
                    "policy": outcome.policy_name,
                    "candidate_counts_by_company": dict(
                        outcome.candidate_counts_by_company
                    ),
                    "candidate_counts_by_company_subquery": dict(
                        outcome.candidate_counts_by_company_subquery
                    ),
                    "selected_counts_by_company": dict(
                        outcome.selected_counts_by_company
                    ),
                    "target_counts_by_company": dict(
                        outcome.target_counts_by_company
                    ),
                    "quota_satisfied": outcome.quota_satisfied,
                    "context_input_tokens": outcome.context_input_tokens,
                    "context_input_limit": outcome.context_input_limit,
                    "candidates": [
                        {
                            "chunk_id": candidate["chunk_id"],
                            "ticker": candidate.get("ticker"),
                            "selected": candidate.get("selected", False),
                            "selection_reason": candidate.get("selection_reason"),
                            "rejection_reason": candidate.get("rejection_reason"),
                            "subquery_matches": candidate.get("subquery_matches", []),
                        }
                        for candidate in outcome.candidates
                    ],
                    "selected_ids": list(outcome.chunk_ids),
                }
            },
        )
        trace.candidate_counts_by_company = dict(outcome.candidate_counts_by_company)
        trace.candidate_counts_by_company_subquery = dict(
            outcome.candidate_counts_by_company_subquery
        )
        trace.dense_backend = getattr(outcome, "dense_backend", "local-npz-exact")
        trace.dense_search_records = list(
            getattr(outcome, "dense_search_records", ())
        )
        trace.qdrant_latency_ms = getattr(outcome, "qdrant_latency_ms", None)
        trace.qdrant_parity_satisfied = getattr(
            outcome, "qdrant_parity_satisfied", None
        )
        trace.candidates = [
            {
                "chunk_id": candidate["chunk_id"],
                "ticker": candidate.get("ticker"),
                "selected": candidate.get("selected", False),
                "selection_reason": candidate.get("selection_reason"),
                "rejection_reason": candidate.get("rejection_reason"),
                "subquery_matches": candidate.get("subquery_matches", []),
            }
            for candidate in outcome.candidates
        ]
        trace.selection = {
            "policy": outcome.policy_name,
            "selected_counts_by_company": dict(outcome.selected_counts_by_company),
            "target_counts_by_company": dict(outcome.target_counts_by_company),
            "quota_satisfied": outcome.quota_satisfied,
            "context_input_tokens": outcome.context_input_tokens,
            "context_input_limit": outcome.context_input_limit,
            "selected_ids": list(outcome.chunk_ids),
            "selection_reasons": {
                candidate["chunk_id"]: candidate.get("selection_reason")
                for candidate in outcome.candidates
                if candidate.get("selected")
            },
        }
        trace.final_generation_evidence_ids = list(outcome.chunk_ids)
        if await disconnected():
            return
        evidence = list(outcome.evidence)
        answer_fragments: list[str] = []
        visible_fragments: list[str] = []
        allowed_ids = list(outcome.chunk_ids)

        if route.uses_calculator:
            if not hasattr(generator, "plan_evidence_calculation"):
                raise RuntimeError("The generator does not support evidence calculations.")
            with trace.stage("calculation_operand_extraction"):
                calculation_plan = await asyncio.to_thread(
                    generator.plan_evidence_calculation,
                    query,
                    evidence,
                    plan["operation"],
                )
            if calculation_plan.ready:
                try:
                    with trace.stage("calculator"):
                        calculation = self.calculator.calculate_operation(
                            calculation_plan.operation,
                            [operand.value for operand in calculation_plan.operands],
                            unit=calculation_plan.result_unit,
                            decimal_places=calculation_plan.decimal_places,
                            input_text=query,
                        )
                except CalculationError:
                    trace.tool_executions.append(
                        {
                            "tool": "calculator",
                            "status": "rejected",
                            "safe_error_class": "invalid_evidence_calculation",
                        }
                    )
                    answer = (
                        "The filing operands could not be combined safely, so I did not "
                        "calculate a result."
                    )
                else:
                    trace.tool_executions.append(
                        {
                            "tool": "calculator",
                            "status": "succeeded",
                            "evidence_derived": True,
                            **calculation.as_dict(),
                        }
                    )
                    cited_operands = []
                    for operand in calculation_plan.operands:
                        citations = ", ".join(operand.source_ids)
                        unit = f" {operand.unit}" if operand.unit else ""
                        cited_operands.append(
                            f"{operand.value}{unit} [{citations}]"
                        )
                    result_unit = (
                        "%"
                        if calculation.unit == "%"
                        else f" {calculation.unit}"
                        if calculation.unit
                        else ""
                    )
                    answer = (
                        "Using "
                        + " and ".join(cited_operands)
                        + f", the {calculation.operation.replace('_', ' ')} is "
                        + f"{calculation.result}{result_unit}."
                    )
                    if self.emit_activity:
                        yield activity_event(f"Calculating {calculation.normalized_expression}")
            else:
                trace.tool_executions.append(
                    {
                        "tool": "calculator",
                        "status": "not_executed",
                        "safe_error_class": calculation_plan.message_code,
                    }
                )
                answer = (
                    "The retrieved filing evidence does not provide unambiguous, "
                    "unit-compatible operands for that calculation."
                )
            answer_fragments.append(answer)
            visible_answer = visible_answer_text(answer, allowed_ids)
            if visible_answer:
                trace.mark_first_token()
                visible_fragments.append(visible_answer)
                yield PipelineEvent("delta", {"text": visible_answer})
        elif self.llm_streaming:
            streaming_generation_started = time.perf_counter()
            if self.emit_activity:
                yield activity_event(random.choice(GENERATION_ACTIVITIES))
            with trace.stage("generation_start"):
                if hasattr(generator, "stream_answer_with_metadata"):
                    if prompt_context:
                        provider_stream = generator.stream_answer_with_metadata(
                            query, evidence, conversation_context=prompt_context
                        )
                    else:
                        provider_stream = generator.stream_answer_with_metadata(
                            query, evidence
                        )
                else:
                    if prompt_context:
                        provider_stream = generator.stream_answer(
                            query, evidence, conversation_context=prompt_context
                        )
                    else:
                        provider_stream = generator.stream_answer(query, evidence)
            sentinel = object()
            citation_filter = CitationVisibilityFilter(allowed_ids)

            def next_fragment() -> object:
                return next(provider_stream, sentinel)

            try:
                while True:
                    fragment = await asyncio.to_thread(next_fragment)
                    if fragment is sentinel:
                        break
                    if await disconnected():
                        return
                    if isinstance(fragment, str) and fragment:
                        answer_fragments.append(fragment)
                        visible_fragment = citation_filter.feed(fragment)
                        if visible_fragment:
                            trace.mark_first_token()
                            visible_fragments.append(visible_fragment)
                            yield PipelineEvent("delta", {"text": visible_fragment})
                tail = citation_filter.finish()
                if tail:
                    trace.mark_first_token()
                    visible_fragments.append(tail)
                    yield PipelineEvent("delta", {"text": tail})
            finally:
                close = getattr(provider_stream, "close", None)
                if callable(close):
                    close()
                trace.provider_usage = dict(getattr(provider_stream, "usage", {}))
                trace.stage_latency_ms["generation"] = round(
                    (time.perf_counter() - streaming_generation_started) * 1_000, 3
                )
        else:
            if self.emit_activity:
                yield activity_event(random.choice(GENERATION_ACTIVITIES))
            with trace.stage("generation"):
                if hasattr(generator, "answer_with_metadata"):
                    if prompt_context:
                        result = await asyncio.to_thread(
                            generator.answer_with_metadata,
                            query,
                            evidence,
                            conversation_context=prompt_context,
                        )
                    else:
                        result = await asyncio.to_thread(
                            generator.answer_with_metadata, query, evidence
                        )
                else:
                    if prompt_context:
                        answer_text = await asyncio.to_thread(
                            generator.answer,
                            query,
                            evidence,
                            conversation_context=prompt_context,
                        )
                    else:
                        answer_text = await asyncio.to_thread(
                            generator.answer, query, evidence
                        )
                    result = GenerationResult(answer_text, {})
            answer = result.text
            trace.provider_usage = result.usage
            if await disconnected():
                return
            if answer:
                answer_fragments.append(answer)
                visible_answer = visible_answer_text(answer, allowed_ids)
                if visible_answer:
                    trace.mark_first_token()
                    visible_fragments.append(visible_answer)
                    yield PipelineEvent("delta", {"text": visible_answer})

        if not answer_fragments:
            raise RuntimeError("The LLM returned no generated text.")
        if not visible_fragments:
            raise RuntimeError("The generated answer contained no visible text.")

        trace.generated_answer = "".join(answer_fragments)
        with trace.stage("citation_resolution"):
            citation_resolution = resolve_cited_evidence(trace.generated_answer, evidence)
        with trace.stage("source_normalization"):
            sources, malformed_count = normalize_sources(
                list(citation_resolution.evidence)
            )
        if citation_resolution.resolved_ids and malformed_count:
            source_status = "cited_with_unrenderable_items"
        elif citation_resolution.resolved_ids:
            source_status = "cited"
        else:
            source_status = "none_cited"
        trace.generated_citation_ids = list(citation_resolution.parsed_ids)
        trace.resolved_used_ids = list(citation_resolution.resolved_ids)
        trace.rejected_citation_ids = list(citation_resolution.rejected_ids)
        trace.source_status = source_status
        yield PipelineEvent(
            "sources",
            {
                "sources": sources,
                "source_status": source_status,
                "malformed_source_count": malformed_count,
            },
            internal={"used_source_ids": list(citation_resolution.resolved_ids)},
        )
        yield PipelineEvent("done", {})


MOCK_NARRATIVE = {
    "company": "Tesla, Inc.",
    "ticker": "TSLA",
    "filing_year": 2025,
    "section": "Item 1 — Business",
    "content_type": "text",
    "text": "Tesla designs, develops, manufactures, leases, and sells electric vehicles and energy generation and storage systems.",
    "source_url": "https://www.sec.gov/Archives/edgar/data/1318605/",
}

MOCK_TABLE = {
    "company": "Mobileye Global Inc.",
    "ticker": "MBLY",
    "filing_year": 2025,
    "section": "Item 8 — Financial Statements",
    "content_type": "table",
    "title": "Illustrative revenue by category",
    "units": "USD millions",
    "headers": ["Category", "2025", "2024", "2023", "2022", "2021", "2020", "2019"],
    "rows": [
        ["Product revenue", "1,613", "1,756", "1,783", "1,691", "1,386", "967", "879"],
        ["Other revenue", "41", "37", "36", "31", "29", "21", "18"],
    ],
    "column_units": [
        "text", "USD millions", "USD millions", "USD millions",
        "USD millions", "USD millions", "USD millions", "USD millions",
    ],
}


class MockPipeline:
    """Explicit deterministic development stream; never used by real mode."""

    mode = "mock"
    ready = True
    answer_delivery = "mock_streaming"

    def __init__(self, delay_seconds: float = 0.06) -> None:
        self.delay_seconds = delay_seconds

    async def stream(
        self,
        query: str,
        is_disconnected: Callable[[], Awaitable[bool]],
        request_id: str | None = None,
        conversation_context: Any | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        document_service: Any | None = None,
        company_scope: list[str] | tuple[str, ...] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[PipelineEvent]:
        await asyncio.sleep(self.delay_seconds)
        if "[mock:pre-error]" in query.casefold():
            raise RuntimeError("Deterministic mock failure before the first token")
        fragments = [
            "AVA found relevant filing evidence. ",
            "This response is arriving as real streamed fragments ",
            "from the isolated development pipeline.",
        ]
        for position, fragment in enumerate(fragments):
            if await is_disconnected():
                return
            yield PipelineEvent("delta", {"text": fragment})
            if "[mock:mid-error]" in query.casefold() and position == 0:
                raise RuntimeError("Deterministic mock failure after partial output")
            await asyncio.sleep(self.delay_seconds)
        yield PipelineEvent(
            "sources",
            {
                "sources": [MOCK_NARRATIVE, MOCK_TABLE],
                "source_status": "cited",
                "malformed_source_count": 0,
            },
        )
        yield PipelineEvent("done", {})


def build_pipeline(
    settings: PipelineSettings,
    provider_settings: ProviderSettings | None = None,
) -> RealPipeline | MockPipeline:
    if settings.mode == "mock":
        return MockPipeline()
    return RealPipeline.build(settings, provider_settings)
