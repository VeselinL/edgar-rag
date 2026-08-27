"""Grounded answer generation over selected filing evidence."""

from .rag import (
    SYSTEM_PROMPT,
    GenerationService,
    format_context,
    resolve_cited_evidence,
)

__all__ = [
    "SYSTEM_PROMPT",
    "GenerationService",
    "format_context",
    "resolve_cited_evidence",
]
