"""Evidence-route handlers shared by the finite request executor."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
import time
from typing import Any

from src.backend.sources import normalize_sources
from src.generation.citations import (
    CitationVisibilityFilter,
    resolve_cited_evidence,
    visible_answer_text,
)
from src.generation.provider import GenerationResult
from src.generation.service import GenerationService
from src.observability import RequestTrace
from src.orchestration.routing import RequestRoute
from src.tools import CalculationError, WebSearchError, infer_calculation_operation


@dataclass(frozen=True)
class PipelineEvent:
    event: str
    data: dict[str, Any]
    internal: dict[str, Any] | None = None


def activity_event(text: str) -> PipelineEvent:
    """Create a safe, deterministic progress update for the waiting bubble."""
    return PipelineEvent("status", {"text": text})


class RouteHandlerMixin:
    """Execute bounded web and upload evidence routes."""
    async def _stream_web_route(
        self,
        query: str,
        route: RequestRoute,
        disconnected: Callable[[], Awaitable[bool]],
        trace: RequestTrace,
        generator: Any,
    ) -> AsyncIterator[PipelineEvent]:
        if self.emit_activity:
            yield activity_event(
                f'Searching web (SEC.gov, Robinhood.com, Reuters.com, Nasdaq.com) for "{query[:160]}"'
            )
        if not self.web_search_enabled:
            answer = (
                "That question needs current or external information, but web search "
                "is disabled in this deployment."
            )
            trace.generated_answer = answer
            trace.source_status = "none_cited"
            trace.mark_first_token()
            yield PipelineEvent("delta", {"text": answer})
            yield PipelineEvent(
                "sources",
                {"sources": [], "source_status": "none_cited", "malformed_source_count": 0},
            )
            yield PipelineEvent("done", {})
            return
        try:
            with trace.stage("web_search"):
                response = await asyncio.to_thread(
                    self.web_search.search,
                    query,
                    max_results=self.web_search_max_results,
                )
        except WebSearchError:
            trace.tool_executions.append(
                {
                    "tool": "web_search",
                    "provider": self.web_search.provider,
                    "status": "failed",
                    "safe_error_class": "web_search_unavailable",
                }
            )
            answer = "Web search is temporarily unavailable, so I can't verify that answer."
            trace.generated_answer = answer
            trace.source_status = "none_cited"
            trace.mark_first_token()
            yield PipelineEvent("delta", {"text": answer})
            yield PipelineEvent(
                "sources",
                {"sources": [], "source_status": "none_cited", "malformed_source_count": 0},
            )
            yield PipelineEvent("done", {})
            return
        trace.tool_executions.append(
            {
                "tool": "web_search",
                "provider": response.provider,
                "status": "succeeded",
                "query": response.query,
                "result_count": len(response.results),
            }
        )
        if not response.results:
            answer = "Web search returned no usable public sources for that question."
            trace.generated_answer = answer
            trace.source_status = "none_cited"
            trace.mark_first_token()
            yield PipelineEvent("delta", {"text": answer})
            yield PipelineEvent(
                "sources",
                {"sources": [], "source_status": "none_cited", "malformed_source_count": 0},
            )
            yield PipelineEvent("done", {})
            return
        evidence = [
            {
                "chunk": {
                    "chunk_id": result.source_id,
                    "content_type": "web",
                    "title": result.title,
                    "publisher": result.publisher,
                    "retrieved_at": result.retrieved_at,
                    "source_url": result.url,
                    "text": result.excerpt,
                }
            }
            for result in response.results
        ]
        trace.final_generation_evidence_ids = [
            result.source_id for result in response.results
        ]
        answer_fragments: list[str] = []
        visible_fragments: list[str] = []
        allowed_ids = trace.final_generation_evidence_ids
        if route.uses_calculator:
            operation = infer_calculation_operation(query)
            if operation is None:
                answer = (
                    "I couldn't identify one supported calculation operation. Please "
                    "specify a difference, ratio, percentage, growth rate, or sum."
                )
            else:
                with trace.stage("calculation_operand_extraction"):
                    calculation_plan = await asyncio.to_thread(
                        generator.plan_evidence_calculation,
                        query,
                        evidence,
                        operation,
                        "web",
                    )
                if not calculation_plan.ready:
                    trace.tool_executions.append(
                        {
                            "tool": "calculator",
                            "status": "not_executed",
                            "safe_error_class": calculation_plan.message_code,
                        }
                    )
                    answer = (
                        "The web results do not provide unambiguous, unit-compatible "
                        "operands for that calculation."
                    )
                else:
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
                        answer = "The web operands could not be combined safely."
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
                            cited_operands.append(f"{operand.value}{unit} [{citations}]")
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
            trace.mark_first_token()
            answer_fragments.append(answer)
            visible_answer = visible_answer_text(answer, allowed_ids)
            if visible_answer:
                visible_fragments.append(visible_answer)
                yield PipelineEvent("delta", {"text": visible_answer})
        elif self.llm_streaming:
            generation_started = time.perf_counter()
            with trace.stage("generation_start"):
                provider_stream = generator.stream_web_answer_with_metadata(
                    query, evidence
                )
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
                    (time.perf_counter() - generation_started) * 1_000, 3
                )
        else:
            with trace.stage("generation"):
                result = await asyncio.to_thread(
                    generator.web_answer_with_metadata, query, evidence
                )
            trace.provider_usage = result.usage
            if result.text:
                trace.mark_first_token()
                answer_fragments.append(result.text)
                visible_answer = visible_answer_text(result.text, allowed_ids)
                if visible_answer:
                    visible_fragments.append(visible_answer)
                    yield PipelineEvent("delta", {"text": visible_answer})
        if not answer_fragments:
            raise RuntimeError("The LLM returned no generated web answer.")
        if not visible_fragments:
            raise RuntimeError("The generated web answer contained no visible text.")
        trace.generated_answer = "".join(answer_fragments)
        citation_resolution = resolve_cited_evidence(trace.generated_answer, evidence)
        sources, malformed_count = normalize_sources(list(citation_resolution.evidence))
        source_status = (
            "cited_with_unrenderable_items"
            if citation_resolution.resolved_ids and malformed_count
            else "cited"
            if citation_resolution.resolved_ids
            else "none_cited"
        )
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
    async def _stream_upload_route(
        self,
        query: str,
        route: RequestRoute,
        disconnected: Callable[[], Awaitable[bool]],
        trace: RequestTrace,
        document_service: Any,
        generator: Any,
        prefetched_results: Sequence[Any] | None = None,
    ) -> AsyncIterator[PipelineEvent]:
        if prefetched_results is None:
            if self.emit_activity:
                yield activity_event("Searching through uploaded documents")
            with trace.stage("uploaded_document_search"):
                results = await asyncio.to_thread(
                    document_service.search,
                    trace.conversation_id,
                    query,
                    limit=10,
                )
        else:
            results = list(prefetched_results)
        if not results:
            answer = "No relevant text was found in the files attached to this chat."
            trace.generated_answer = answer
            trace.source_status = "none_cited"
            trace.mark_first_token()
            yield PipelineEvent("delta", {"text": answer})
            yield PipelineEvent(
                "sources",
                {"sources": [], "source_status": "none_cited", "malformed_source_count": 0},
            )
            yield PipelineEvent("done", {})
            return
        evidence = [
            {
                "chunk": {
                    "chunk_id": result.source_id,
                    "content_type": "upload",
                    "document_id": result.document_id,
                    "filename": result.filename,
                    "media_type": result.media_type,
                    "page_number": result.page_number,
                    "text": result.text,
                }
            }
            for result in results
        ]
        allowed_ids = [result.source_id for result in results]
        trace.final_generation_evidence_ids = allowed_ids
        trace.selected_asset_ids = list(
            dict.fromkeys(result.document_id for result in results)
        )
        answer_fragments: list[str] = []
        visible_fragments: list[str] = []
        if route.uses_calculator:
            operation = infer_calculation_operation(query)
            if operation is None:
                answer = (
                    "I couldn't identify one supported calculation operation. Please "
                    "specify a difference, ratio, percentage, growth rate, or sum."
                )
            else:
                with trace.stage("calculation_operand_extraction"):
                    calculation_plan = await asyncio.to_thread(
                        generator.plan_evidence_calculation,
                        query,
                        evidence,
                        operation,
                        "upload",
                    )
                if not calculation_plan.ready:
                    trace.tool_executions.append(
                        {
                            "tool": "calculator",
                            "status": "not_executed",
                            "safe_error_class": calculation_plan.message_code,
                        }
                    )
                    answer = (
                        "The attached files do not provide unambiguous, unit-compatible "
                        "operands for that calculation."
                    )
                else:
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
                        answer = "The uploaded-document operands could not be combined safely."
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
                            cited_operands.append(f"{operand.value}{unit} [{citations}]")
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
            answer_fragments.append(answer)
            visible = visible_answer_text(answer, allowed_ids)
            if visible:
                trace.mark_first_token()
                visible_fragments.append(visible)
                yield PipelineEvent("delta", {"text": visible})
        elif self.llm_streaming:
            generation_started = time.perf_counter()
            with trace.stage("generation_start"):
                provider_stream = generator.stream_upload_answer_with_metadata(
                    query, evidence
                )
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
                        visible = citation_filter.feed(fragment)
                        if visible:
                            trace.mark_first_token()
                            visible_fragments.append(visible)
                            yield PipelineEvent("delta", {"text": visible})
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
                    (time.perf_counter() - generation_started) * 1_000, 3
                )
        else:
            with trace.stage("generation"):
                generated = await asyncio.to_thread(
                    generator.upload_answer_with_metadata, query, evidence
                )
            trace.provider_usage = generated.usage
            if generated.text:
                answer_fragments.append(generated.text)
                visible = visible_answer_text(generated.text, allowed_ids)
                if visible:
                    trace.mark_first_token()
                    visible_fragments.append(visible)
                    yield PipelineEvent("delta", {"text": visible})
        if not answer_fragments or not visible_fragments:
            raise RuntimeError("The generated uploaded-document answer was empty.")
        trace.generated_answer = "".join(answer_fragments)
        citation_resolution = resolve_cited_evidence(trace.generated_answer, evidence)
        sources, malformed_count = normalize_sources(list(citation_resolution.evidence))
        source_status = (
            "cited_with_unrenderable_items"
            if citation_resolution.resolved_ids and malformed_count
            else "cited"
            if citation_resolution.resolved_ids
            else "none_cited"
        )
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
