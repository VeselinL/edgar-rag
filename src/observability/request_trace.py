"""One redacted structured record for each AVA request."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any, Iterator
from uuid import uuid4


def safe_error_class(error: BaseException) -> str:
    """Return a stable class without provider messages, credentials, or traces."""
    name = type(error).__name__
    if name in {"EvidencePolicyError", "EvidencePackingError"}:
        return name
    if isinstance(error, (TimeoutError, ConnectionError)):
        return "provider_transport_error"
    if isinstance(error, (ValueError, KeyError, TypeError)):
        return "validation_error"
    return "pipeline_error"


@dataclass
class RequestTrace:
    """Mutable request collector serialized once when the stream terminates."""

    original_query: str
    request_id: str = field(default_factory=lambda: str(uuid4()))
    conversation_id: str | None = None
    turn_id: str | None = None
    corpus_version: str = "unknown"
    index_version: str = "local-npz-bm25"
    answer_delivery: str = "unknown"
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    stage_latency_ms: dict[str, float] = field(default_factory=dict)
    retrieval_subqueries: list[str] = field(default_factory=list)
    resolver: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    candidate_counts_by_company: dict[str, int] = field(default_factory=dict)
    candidate_counts_by_company_subquery: dict[str, int] = field(default_factory=dict)
    selection: dict[str, Any] = field(default_factory=dict)
    final_generation_evidence_ids: list[str] = field(default_factory=list)
    generated_answer: str | None = None
    generated_citation_ids: list[str] = field(default_factory=list)
    resolved_used_ids: list[str] = field(default_factory=list)
    rejected_citation_ids: list[str] = field(default_factory=list)
    source_status: str | None = None
    provider_usage: dict[str, int] = field(default_factory=dict)
    time_to_first_token_ms: float | None = None
    cancelled: bool = False
    safe_error_class: str | None = None
    image_candidates: list[str] = field(default_factory=list)
    selected_asset_ids: list[str] = field(default_factory=list)
    short_term_memory_ids: list[str] = field(default_factory=list)
    long_term_memory_ids: list[str] = field(default_factory=list)
    reranker: dict[str, Any] = field(
        default_factory=lambda: {"enabled": False, "identity": None, "version": None}
    )
    _started_monotonic: float = field(default_factory=time.perf_counter, repr=False)

    def __post_init__(self) -> None:
        if self.turn_id is None:
            self.turn_id = self.request_id

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.stage_latency_ms[name] = round(
                (time.perf_counter() - started) * 1_000, 3
            )

    def mark_first_token(self) -> None:
        if self.time_to_first_token_ms is None:
            self.time_to_first_token_ms = round(
                (time.perf_counter() - self._started_monotonic) * 1_000, 3
            )

    def as_record(self) -> dict[str, Any]:
        complete_latency = round(
            (time.perf_counter() - self._started_monotonic) * 1_000, 3
        )
        return {
            "schema_version": 1,
            "event": "ava_request_completed",
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "started_at": self.started_at,
            "corpus_version": self.corpus_version,
            "index_version": self.index_version,
            "answer_delivery": self.answer_delivery,
            "original_query": self.original_query,
            "retrieval_subqueries": self.retrieval_subqueries,
            "resolver": self.resolver,
            "candidate_counts_by_company": self.candidate_counts_by_company,
            "candidate_counts_by_company_subquery": self.candidate_counts_by_company_subquery,
            "candidates": self.candidates,
            "reranker": self.reranker,
            "selection": self.selection,
            "image_candidates": self.image_candidates,
            "selected_asset_ids": self.selected_asset_ids,
            "short_term_memory_ids": self.short_term_memory_ids,
            "long_term_memory_ids": self.long_term_memory_ids,
            "final_generation_evidence_ids": self.final_generation_evidence_ids,
            "generated_answer": self.generated_answer,
            "generated_citation_ids": self.generated_citation_ids,
            "resolved_used_ids": self.resolved_used_ids,
            "rejected_citation_ids": self.rejected_citation_ids,
            "source_status": self.source_status,
            "stage_latency_ms": self.stage_latency_ms,
            "time_to_first_token_ms": self.time_to_first_token_ms,
            "complete_latency_ms": complete_latency,
            "provider_usage": self.provider_usage,
            "cancelled": self.cancelled,
            "safe_error_class": self.safe_error_class,
        }
