"""Typed evidence budgets and fail-closed token-packing contracts."""

from __future__ import annotations

from dataclasses import dataclass


class EvidencePolicyError(ValueError):
    """The request cannot be represented by the configured evidence policy."""


class EvidencePackingError(RuntimeError):
    """Complete evidence chunks cannot satisfy quotas inside the token budget."""


@dataclass(frozen=True)
class EvidenceBudgetPolicy:
    """One source of truth for candidate, quota, supplement, and token budgets."""

    name: str = "company-balanced-token-aware"
    version: int = 1
    candidate_k_per_company: int = 10
    minimum_final_per_company: int = 5
    single_company_total: int = 10
    two_company_supplemental: int = 5
    three_company_supplemental: int = 7
    four_plus_supplemental: int | None = None
    global_final_total: int = 10
    minimum_final_per_subquery: int = 2
    multi_subquery_bonus: float = 0.01
    context_window_tokens: int = 32_768
    reserved_output_tokens: int = 4_096

    def __post_init__(self) -> None:
        positive = {
            "candidate_k_per_company": self.candidate_k_per_company,
            "minimum_final_per_company": self.minimum_final_per_company,
            "single_company_total": self.single_company_total,
            "global_final_total": self.global_final_total,
            "minimum_final_per_subquery": self.minimum_final_per_subquery,
            "context_window_tokens": self.context_window_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError("Evidence policy values must be positive: " + ", ".join(invalid))
        if self.candidate_k_per_company < self.minimum_final_per_company:
            raise ValueError("Candidate depth cannot be smaller than the company minimum.")
        if self.single_company_total < self.minimum_final_per_company:
            raise ValueError("Single-company total cannot be smaller than its minimum.")
        supplements = {
            "two_company_supplemental": self.two_company_supplemental,
            "three_company_supplemental": self.three_company_supplemental,
        }
        if self.four_plus_supplemental is not None:
            supplements["four_plus_supplemental"] = self.four_plus_supplemental
        invalid_supplements = [
            name for name, value in supplements.items() if value < 0
        ]
        if invalid_supplements:
            raise ValueError(
                "Supplemental evidence counts cannot be negative: "
                + ", ".join(invalid_supplements)
            )
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

    def supplemental_slots(self, company_count: int) -> int:
        if company_count <= 1:
            return self.single_company_total - self.minimum_final_per_company
        if company_count == 2:
            return self.two_company_supplemental
        if company_count == 3:
            return self.three_company_supplemental
        if self.four_plus_supplemental is None:
            raise EvidencePolicyError(
                "Explicit requests for four or more companies require a configured "
                "four_plus_supplemental evidence budget."
            )
        return self.four_plus_supplemental

    def final_total(self, company_count: int) -> int:
        if company_count <= 0:
            return self.global_final_total
        return (
            company_count * self.minimum_final_per_company
            + self.supplemental_slots(company_count)
        )


DEFAULT_EVIDENCE_POLICY = EvidenceBudgetPolicy()
