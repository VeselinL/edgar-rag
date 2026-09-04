"""Typed contracts shared by AVA's bounded route-and-tool orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.filings.corpus import ACTIVE_FILINGS


class Freshness(StrEnum):
    NONE = "none"
    MARKET_LIVE = "market_live"
    LEADERSHIP_CURRENT = "leadership_current"
    COMPANY_NEWS = "company_news"
    REGULATORY_CURRENT = "regulatory_current"


class EvidenceSource(StrEnum):
    FILING = "filing"
    UPLOAD = "upload"
    MEMORY = "memory"
    WEB = "web"


class TrustedSourceKey(StrEnum):
    SEC_EDGAR = "sec_edgar"
    ISSUER_OFFICIAL = "issuer_official"
    VEHICLE_REGULATOR = "vehicle_regulator"
    MARKET_PRIMARY = "market_primary"
    MARKET_SECONDARY = "market_secondary"
    NEWS_INDEPENDENT = "news_independent"


class RouteKind(StrEnum):
    CONVERSATION = "conversation"
    CLARIFY = "clarify"
    FILING = "filing"
    UPLOAD = "upload"
    FILING_UPLOAD = "filing_upload"
    WEB = "web"
    CALCULATE = "calculate"
    FILING_CALCULATE = "filing_calculate"
    UPLOAD_CALCULATE = "upload_calculate"
    WEB_CALCULATE = "web_calculate"

    # Temporary source-compatible names while callers move to the typed plan.
    CONVERSATION_ONLY = "conversation"
    FILING_RAG = "filing"
    UPLOADED_DOCUMENT_RAG = "upload"
    WEB_SEARCH = "web"
    CALCULATOR = "calculate"
    FILING_AND_CALCULATOR = "filing_calculate"
    UPLOAD_AND_CALCULATOR = "upload_calculate"
    WEB_AND_CALCULATOR = "web_calculate"


class RouteReason(StrEnum):
    GREETING = "greeting"
    AVA_HELP = "ava_help"
    CASUAL_CONVERSATION = "casual_conversation"
    FILING_EVIDENCE = "filing_evidence"
    UPLOADED_EVIDENCE = "uploaded_evidence"
    CURRENT_OR_EXTERNAL = "current_or_external"
    PURE_ARITHMETIC = "pure_arithmetic"
    EVIDENCE_ARITHMETIC = "evidence_arithmetic"
    AMBIGUOUS_INTENT = "ambiguous_intent"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True)
class AtomicQuery:
    query: str
    tickers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.query.strip() or any(ticker not in ACTIVE_FILINGS for ticker in self.tickers):
            raise ValueError("Atomic query contains an invalid query or ticker.")


@dataclass(frozen=True)
class CalculationRequest:
    operation: str

    def __post_init__(self) -> None:
        if self.operation not in {
            "add",
            "subtract",
            "multiply",
            "divide",
            "percentage",
            "difference",
            "ratio",
            "growth_rate",
            "sum",
        }:
            raise ValueError("Calculation request contains an unsupported operation.")


@dataclass(frozen=True)
class EvidencePlan:
    """One finite, server-validated evidence and tool execution plan."""

    route: RouteKind
    reason_code: RouteReason
    arithmetic_required: bool = False
    decided_by: str = "model"
    resolved_tickers: tuple[str, ...] = ()
    selected_company_scope: tuple[str, ...] = ()
    subqueries: tuple[AtomicQuery, ...] = ()
    freshness: Freshness = Freshness.NONE
    required_sources: tuple[EvidenceSource, ...] = ()
    web_source_keys: tuple[TrustedSourceKey, ...] = ()
    calculation: CalculationRequest | None = None
    clarification: str | None = None
    maximum_steps: int = 4

    def __post_init__(self) -> None:
        if not isinstance(self.route, RouteKind) or not isinstance(
            self.reason_code, RouteReason
        ):
            raise ValueError("Evidence plan contains an invalid route or reason.")
        for tickers in (self.resolved_tickers, self.selected_company_scope):
            if len(tickers) != len(set(tickers)) or any(
                ticker not in ACTIVE_FILINGS for ticker in tickers
            ):
                raise ValueError("Evidence plan contains an invalid company scope.")
        if not 1 <= self.maximum_steps <= 4:
            raise ValueError("Evidence plan exceeds the finite execution limit.")
        if self.clarification is not None and (
            not self.clarification.strip() or len(self.clarification) > 500
        ):
            raise ValueError("Evidence plan contains an invalid clarification.")
        if self.arithmetic_required != (self.calculation is not None):
            # Legacy construction supplies only arithmetic_required; keep the
            # typed operation unset until the validated operand planner runs.
            if not self.arithmetic_required or self.calculation is not None:
                raise ValueError("Evidence plan calculation fields disagree.")

    def with_scope(
        self,
        *,
        resolved_tickers: tuple[str, ...],
        selected_company_scope: tuple[str, ...],
        subqueries: tuple[AtomicQuery, ...] = (),
    ) -> "EvidencePlan":
        from dataclasses import replace

        return replace(
            self,
            resolved_tickers=resolved_tickers,
            selected_company_scope=selected_company_scope,
            subqueries=subqueries,
        )


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
