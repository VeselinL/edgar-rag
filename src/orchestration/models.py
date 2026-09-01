"""Typed contracts shared by AVA's bounded route-and-tool orchestration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceOperand:
    label: str
    value: str
    verbatim_value: str
    unit: str | None
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceCalculationPlan:
    status: str
    operation: str
    operands: tuple[EvidenceOperand, ...]
    result_unit: str | None
    decimal_places: int | None
    message_code: str | None

    @property
    def ready(self) -> bool:
        return self.status == "ready"
