"""Reusable retrieval components for the filing corpus."""

from .scope_aware import (
    COMPANY_ALIASES,
    COMPARISON_CUES,
    DEFAULT_FINAL_EVIDENCE_K,
    RetrievalOutcome,
    ScopeAwareRetriever,
    detect_companies,
    detect_scope,
    resolve_comparison_targets,
    scope_aware_hybrid_retrieve,
)

__all__ = [
    "COMPANY_ALIASES",
    "COMPARISON_CUES",
    "DEFAULT_FINAL_EVIDENCE_K",
    "RetrievalOutcome",
    "ScopeAwareRetriever",
    "detect_companies",
    "detect_scope",
    "resolve_comparison_targets",
    "scope_aware_hybrid_retrieve",
]
