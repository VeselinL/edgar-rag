"""Compatibility facade for AVA request execution."""

from src.backend.dependencies import (
    FILINGS,
    PROJECT_ROOT,
    build_bm25_index,
    corpus_version,
    load_corpus,
)
from src.orchestration.executor import (
    AVAILABLE_MODELS,
    MockPipeline,
    PipelineEvent,
    PipelineSettings,
    RealPipeline,
    activity_event,
    ava_introduction,
    build_pipeline,
    company_scope_mismatch_message,
    infer_filing_scope_query,
    uploaded_evidence_matches_query,
    without_calculator_route,
)

__all__ = [name for name in globals() if not name.startswith("_")]
