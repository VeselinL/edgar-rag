"""Typed evidence budgets and fail-closed token-packing contracts."""

from __future__ import annotations

from dataclasses import dataclass


class EvidencePolicyError(ValueError):
    """The request cannot be represented by the configured evidence policy."""


class EvidencePackingError(RuntimeError):
    """Complete evidence chunks cannot satisfy quotas inside the token budget."""


@dataclass(frozen=True)
class EvidenceBudgetPolicy:
    """One source of truth for candidate, final-evidence, and token budgets."""

    name: str = "company-balanced-token-aware"
    version: int = 2
    candidate_k_per_company: int = 10
    per_company_final_limit: int = 10
    corpus_final_limit: int = 50
    global_final_total: int = 10
    minimum_final_per_subquery: int = 2
    multi_subquery_bonus: float = 0.01
    context_window_tokens: int = 32_768
    reserved_output_tokens: int = 4_096

    def __post_init__(self) -> None:
        positive = {
            "candidate_k_per_company": self.candidate_k_per_company,
            "per_company_final_limit": self.per_company_final_limit,
            "corpus_final_limit": self.corpus_final_limit,
            "global_final_total": self.global_final_total,
            "minimum_final_per_subquery": self.minimum_final_per_subquery,
            "context_window_tokens": self.context_window_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError("Evidence policy values must be positive: " + ", ".join(invalid))
        if self.candidate_k_per_company < self.per_company_final_limit:
            raise ValueError("Candidate depth cannot be smaller than the company limit.")
        if self.minimum_final_per_subquery > self.candidate_k_per_company:
            raise ValueError(
                "The per-subquery minimum cannot exceed the company candidate depth."
            )
        if self.reserved_output_tokens >= self.context_window_tokens:
            raise ValueError("Reserved output tokens must be smaller than the context window.")
        if self.multi_subquery_bonus < 0:
            raise ValueError("Multi-subquery bonus must be non-negative.")

    @property
    def policy_id(self) -> str:
        return f"{self.name}-v{self.version}"

    @property
    def input_token_limit(self) -> int:
        return self.context_window_tokens - self.reserved_output_tokens

    def final_total(self, company_count: int) -> int:
        if company_count <= 0:
            return min(self.global_final_total, self.corpus_final_limit)
        return min(
            company_count * self.per_company_final_limit,
            self.corpus_final_limit,
        )

    def company_target_counts(self, company_count: int) -> tuple[int, ...]:
        """Return a deterministic, maximally even allocation under both hard caps."""
        if company_count <= 0:
            return ()
        total = self.final_total(company_count)
        base, remainder = divmod(total, company_count)
        return tuple(
            min(self.per_company_final_limit, base + (index < remainder))
            for index in range(company_count)
        )


DEFAULT_EVIDENCE_POLICY = EvidenceBudgetPolicy()
