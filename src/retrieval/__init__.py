"""Reusable retrieval components for the filing corpus."""

from .evidence_policy import (
    DEFAULT_EVIDENCE_POLICY,
    EvidenceBudgetPolicy,
    EvidencePackingError,
    EvidencePolicyError,
)
from .scope_aware import (
    COMPANY_ALIASES,
    COMPARISON_CUES,
    DEFAULT_FINAL_EVIDENCE_K,
    DEFAULT_MIN_CHUNKS_PER_SUBQUERY,
    DEFAULT_MULTI_SUBQUERY_BONUS,
    DEFAULT_SUBQUERY_RETRIEVAL_K,
    RetrievalOutcome,
    ScopeAwareRetriever,
    detect_companies,
    detect_scope,
    resolve_comparison_targets,
    retrieve_generation_context,
    scope_aware_hybrid_retrieve,
)

__all__ = [
    "COMPANY_ALIASES",
    "COMPARISON_CUES",
    "DEFAULT_EVIDENCE_POLICY",
    "DEFAULT_FINAL_EVIDENCE_K",
    "DEFAULT_MIN_CHUNKS_PER_SUBQUERY",
    "DEFAULT_MULTI_SUBQUERY_BONUS",
    "DEFAULT_SUBQUERY_RETRIEVAL_K",
    "EvidenceBudgetPolicy",
    "EvidencePackingError",
    "EvidencePolicyError",
    "RetrievalOutcome",
    "ScopeAwareRetriever",
    "detect_companies",
    "detect_scope",
    "resolve_comparison_targets",
    "retrieve_generation_context",
    "scope_aware_hybrid_retrieve",
]
