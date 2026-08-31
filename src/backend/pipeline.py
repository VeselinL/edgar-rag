"""Runtime assembly and stream orchestration for AVA."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, replace
import hashlib
import json
import logging
import os
from pathlib import Path
import resource
import time
from typing import Any
from uuid import uuid4

import bm25s
import dotenv
import numpy as np
from sentence_transformers import SentenceTransformer

from src.embeddings.embed_chunks import MODEL_CONFIGS
from src.filings.corpus import ACTIVE_FILINGS
from src.generation.rag import (
    GenerationResult,
    GenerationService,
    ProviderCircuitBreaker,
    count_generation_input_tokens,
    make_llm_client,
    resolve_cited_evidence,
)
from src.indexing.qdrant_index import (
    DEFAULT_ALIAS,
    DEFAULT_QDRANT_URL,
    alias_target,
    make_client,
)
from src.observability import RequestTrace, safe_error_class
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

from .sources import normalize_sources


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FILINGS = ACTIVE_FILINGS
LOGGER = logging.getLogger(__name__)
TelemetrySink = Callable[[dict[str, Any]], None]


def corpus_version(chunks: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk["chunk_id"].encode())
        digest.update(b"\0")
        digest.update(chunk.get("source_processed_sha256", "").encode())
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


@dataclass(frozen=True)
class PipelineEvent:
    event: str
    data: dict[str, Any]
    internal: dict[str, Any] | None = None


@dataclass(frozen=True)
class PipelineSettings:
    mode: str = "real"
    model_device: str = "cpu"
    llm_model: str = "AZURE_GPT_51_2025_1113"
    llm_streaming: bool = True
    context_window_tokens: int = 32_768
    reserved_output_tokens: int = 4_096
    observability_retention_days: int = 30
    qdrant_mode: str = "disabled"
    qdrant_url: str = DEFAULT_QDRANT_URL
    qdrant_api_key: str | None = None
    qdrant_collection_alias: str = DEFAULT_ALIAS
    qdrant_local_path: str | None = None
    qdrant_timeout_seconds: int = 30

    @classmethod
    def from_environment(cls) -> "PipelineSettings":
        # Keep direct production-module/CLI use consistent with FastAPI startup.
        # Existing process environment values retain precedence over `.env`.
        dotenv.load_dotenv(PROJECT_ROOT / ".env")
        mode = os.getenv("AVA_PIPELINE_MODE", "real").strip().casefold()
        if mode not in {"real", "mock"}:
            raise ValueError("AVA_PIPELINE_MODE must be 'real' or 'mock'.")
        raw_streaming = os.getenv("AVA_LLM_STREAMING", "true").strip().casefold()
        if raw_streaming not in {"true", "false"}:
            raise ValueError("AVA_LLM_STREAMING must be 'true' or 'false'.")
        qdrant_mode = os.getenv("AVA_QDRANT_MODE", "disabled").strip().casefold()
        if qdrant_mode not in {"disabled", "shadow", "primary"}:
            raise ValueError(
                "AVA_QDRANT_MODE must be 'disabled', 'shadow', or 'primary'."
            )
        context_window_tokens = int(os.getenv("AVA_LLM_CONTEXT_WINDOW_TOKENS", "32768"))
        reserved_output_tokens = int(os.getenv("AVA_LLM_RESERVED_OUTPUT_TOKENS", "4096"))
        observability_retention_days = int(
            os.getenv("AVA_OBSERVABILITY_RETENTION_DAYS", "30")
        )
        qdrant_timeout_seconds = int(os.getenv("QDRANT_TIMEOUT_SECONDS", "30"))
        if context_window_tokens <= 0 or reserved_output_tokens <= 0:
            raise ValueError("AVA LLM token budgets must be positive.")
        if observability_retention_days <= 0:
            raise ValueError("AVA_OBSERVABILITY_RETENTION_DAYS must be positive.")
        if qdrant_timeout_seconds <= 0:
            raise ValueError("QDRANT_TIMEOUT_SECONDS must be positive.")
        qdrant_local_path = os.getenv("QDRANT_LOCAL_PATH", "").strip() or None
        return cls(
            mode=mode,
            model_device=os.getenv("AVA_MODEL_DEVICE", "cpu"),
            llm_model=os.getenv("AVA_LLM_MODEL", "AZURE_GPT_51_2025_1113"),
            llm_streaming=raw_streaming == "true",
            context_window_tokens=context_window_tokens,
            reserved_output_tokens=reserved_output_tokens,
            observability_retention_days=observability_retention_days,
            qdrant_mode=qdrant_mode,
            qdrant_url=os.getenv("QDRANT_URL", DEFAULT_QDRANT_URL).strip(),
            qdrant_api_key=os.getenv("QDRANT_API_KEY") or None,
            qdrant_collection_alias=os.getenv(
                "QDRANT_COLLECTION_ALIAS", DEFAULT_ALIAS
            ).strip(),
            qdrant_local_path=qdrant_local_path,
            qdrant_timeout_seconds=qdrant_timeout_seconds,
        )


def load_corpus(
    project_root: Path = PROJECT_ROOT,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    company_embeddings = []
    all_chunks: list[dict[str, Any]] = []
    for ticker, filing_name in FILINGS.items():
        embedding_paths = list(
            (project_root / "data" / "embeddings" / ticker).glob(
                f"{filing_name}.bgebase*.npz"
            )
        )
        if len(embedding_paths) != 1:
            raise ValueError(
                f"Expected one BGE-base vector artifact for {ticker}; found {len(embedding_paths)}."
            )
        with np.load(embedding_paths[0]) as archive:
            embeddings = archive["embeddings"]
        chunk_path = project_root / "data" / "chunks" / ticker / f"{filing_name}.chunks.jsonl"
        with chunk_path.open(encoding="utf-8") as file:
            chunks = [json.loads(line) for line in file if line.strip()]
        if len(embeddings) != len(chunks):
            raise ValueError(f"{ticker}: embedding and chunk counts differ.")
        company_embeddings.append(embeddings)
        all_chunks.extend(chunks)
    matrix = np.vstack(company_embeddings)
    if len(matrix) != len(all_chunks):
        raise ValueError("Full embedding and chunk corpus counts differ.")
    return matrix, all_chunks


def build_bm25_index(chunks: list[dict[str, Any]]) -> bm25s.BM25:
    texts = [chunk.get("text", "") for chunk in chunks]
    if any(not text.strip() for text in texts):
        raise ValueError("Every chunk must have searchable text.")
    retriever = bm25s.BM25()
    retriever.index(bm25s.tokenize(texts))
    return retriever


class RealPipeline:
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

    @staticmethod
    def _log_trace(record: dict[str, Any]) -> None:
        LOGGER.info("AVA request completed", extra={"ava_request": record})

    def close(self) -> None:
        close_provider = getattr(self.generator.client, "close", None)
        if callable(close_provider):
            close_provider()
        dense = self.retriever.dense_retriever
        if isinstance(dense, ShadowDenseRetriever):
            dense = dense.shadow
        client = getattr(dense, "client", None)
        close_qdrant = getattr(client, "close", None)
        if callable(close_qdrant):
            close_qdrant()

    @classmethod
    def build(cls, settings: PipelineSettings) -> "RealPipeline":
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
        generator = GenerationService(
            make_llm_client(),
            model=settings.llm_model,
            max_output_tokens=settings.reserved_output_tokens,
            circuit_breaker=ProviderCircuitBreaker(
                failure_threshold=int(os.getenv("AVA_PROVIDER_CIRCUIT_FAILURES", "5")),
                recovery_seconds=float(os.getenv("AVA_PROVIDER_CIRCUIT_RECOVERY_SECONDS", "30")),
            ),
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
        )

    async def stream(
        self,
        query: str,
        is_disconnected: Callable[[], Awaitable[bool]],
        request_id: str | None = None,
        conversation_context: Any | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
    ) -> AsyncIterator[PipelineEvent]:
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
                query, is_disconnected, trace, conversation_context
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
    ) -> AsyncIterator[PipelineEvent]:
        async def disconnected() -> bool:
            value = await is_disconnected()
            if value:
                trace.cancelled = True
            return value

        prompt_context = (
            conversation_context.prompt_text()
            if conversation_context is not None
            else ""
        )
        if conversation_context is not None:
            trace.short_term_memory_ids = list(conversation_context.short_term_ids)
            trace.long_term_memory_ids = list(conversation_context.long_term_ids)
        with trace.stage("deterministic_resolution"):
            deterministic_resolution = self.company_resolver.resolve(query)
        with trace.stage("planning"):
            if prompt_context:
                plan = await asyncio.to_thread(
                    self.generator.plan_retrieval,
                    query,
                    deterministic_resolution,
                    prompt_context,
                )
            else:
                plan = await asyncio.to_thread(
                    self.generator.plan_retrieval, query, deterministic_resolution
                )
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

        if self.llm_streaming:
            streaming_generation_started = time.perf_counter()
            with trace.stage("generation_start"):
                if hasattr(self.generator, "stream_answer_with_metadata"):
                    if prompt_context:
                        provider_stream = self.generator.stream_answer_with_metadata(
                            query, evidence, conversation_context=prompt_context
                        )
                    else:
                        provider_stream = self.generator.stream_answer_with_metadata(
                            query, evidence
                        )
                else:
                    if prompt_context:
                        provider_stream = self.generator.stream_answer(
                            query, evidence, conversation_context=prompt_context
                        )
                    else:
                        provider_stream = self.generator.stream_answer(query, evidence)
            sentinel = object()

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
                        trace.mark_first_token()
                        answer_fragments.append(fragment)
                        yield PipelineEvent("delta", {"text": fragment})
            finally:
                close = getattr(provider_stream, "close", None)
                if callable(close):
                    close()
                trace.provider_usage = dict(getattr(provider_stream, "usage", {}))
                trace.stage_latency_ms["generation"] = round(
                    (time.perf_counter() - streaming_generation_started) * 1_000, 3
                )
        else:
            with trace.stage("generation"):
                if hasattr(self.generator, "answer_with_metadata"):
                    if prompt_context:
                        result = await asyncio.to_thread(
                            self.generator.answer_with_metadata,
                            query,
                            evidence,
                            conversation_context=prompt_context,
                        )
                    else:
                        result = await asyncio.to_thread(
                            self.generator.answer_with_metadata, query, evidence
                        )
                else:
                    if prompt_context:
                        answer_text = await asyncio.to_thread(
                            self.generator.answer,
                            query,
                            evidence,
                            conversation_context=prompt_context,
                        )
                    else:
                        answer_text = await asyncio.to_thread(
                            self.generator.answer, query, evidence
                        )
                    result = GenerationResult(answer_text, {})
            answer = result.text
            trace.provider_usage = result.usage
            if await disconnected():
                return
            if answer:
                trace.mark_first_token()
                answer_fragments.append(answer)
                yield PipelineEvent("delta", {"text": answer})

        if not answer_fragments:
            raise RuntimeError("The LLM returned no generated text.")

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


def build_pipeline(settings: PipelineSettings) -> RealPipeline | MockPipeline:
    if settings.mode == "mock":
        return MockPipeline()
    return RealPipeline.build(settings)
