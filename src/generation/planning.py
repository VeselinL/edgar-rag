"""Typed parsing for evidence-linked calculation plans."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from src.orchestration.models import EvidenceCalculationPlan, EvidenceOperand
from src.tools import CalculationError, parse_evidence_number


def parse_evidence_calculation_plan(
    payload: str | dict[str, Any],
    evidence: Sequence[dict[str, Any]],
    expected_operation: str,
    *,
    require_periods: bool = False,
) -> EvidenceCalculationPlan:
    """Validate source-linked operands before deterministic calculation."""
    if isinstance(payload, str):
        raw = payload.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        if not raw:
            raise RuntimeError("Calculation planner returned empty content.")
        value = json.loads(raw)
    else:
        value = payload
    required = {
        "status",
        "operation",
        "operands",
        "result_unit",
        "decimal_places",
        "message_code",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Calculation planner returned an invalid object.")
    supported_operations = {"percentage", "difference", "ratio", "growth_rate", "sum"}
    if expected_operation not in supported_operations or value["operation"] != expected_operation:
        raise ValueError("Calculation planner changed the requested operation.")
    if value["status"] not in {"ready", "missing"}:
        raise ValueError("Calculation planner returned an invalid status.")
    result_unit = value["result_unit"]
    decimal_places = value["decimal_places"]
    message_code = value["message_code"]
    if result_unit is not None and (
        not isinstance(result_unit, str)
        or not result_unit.strip()
        or len(result_unit) > 80
        or "\n" in result_unit
    ):
        raise ValueError("Calculation planner returned an invalid result unit.")
    if decimal_places is not None and (
        not isinstance(decimal_places, int)
        or isinstance(decimal_places, bool)
        or not 0 <= decimal_places <= 24
    ):
        raise ValueError("Calculation planner returned invalid rounding.")
    missing_codes = {
        "missing_operand",
        "ambiguous_operand",
        "incompatible_units",
        "unsupported_operation",
    }
    if value["status"] == "missing":
        if message_code not in missing_codes:
            raise ValueError("Calculation planner omitted its missing-evidence code.")
        return EvidenceCalculationPlan(
            "missing",
            expected_operation,
            (),
            result_unit,
            decimal_places,
            message_code,
        )
    if message_code is not None:
        raise ValueError("A ready calculation plan cannot include a missing-evidence code.")
    raw_operands = value["operands"]
    expected_count = 2 if expected_operation != "sum" else None
    if (
        not isinstance(raw_operands, list)
        or len(raw_operands) < 2
        or len(raw_operands) > 10
        or (expected_count is not None and len(raw_operands) != expected_count)
    ):
        raise ValueError("Calculation planner returned the wrong operand count.")
    evidence_by_id = {
        item.get("chunk", item)["chunk_id"]: item.get("chunk", item)
        for item in evidence
    }
    operands: list[EvidenceOperand] = []
    for item in raw_operands:
        expected_fields = {
            "label",
            "value",
            "verbatim_value",
            "unit",
            "source_ids",
        }
        if require_periods:
            expected_fields.add("period")
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise ValueError("Calculation planner returned an invalid operand object.")
        label = item["label"]
        numeric_value = item["value"]
        verbatim = item["verbatim_value"]
        unit = item["unit"]
        source_ids = item["source_ids"]
        if isinstance(numeric_value, (int, float)) and not isinstance(
            numeric_value, bool
        ):
            numeric_value = str(numeric_value)
        if (
            not isinstance(label, str)
            or not label.strip()
            or len(label) > 160
            or "\n" in label
            or not isinstance(numeric_value, str)
            or not isinstance(verbatim, str)
            or not verbatim.strip()
            or len(verbatim) > 80
            or "\n" in verbatim
            or unit is not None
            and (
                not isinstance(unit, str)
                or not unit.strip()
                or len(unit) > 80
                or "\n" in unit
            )
            or not isinstance(source_ids, list)
            or not source_ids
            or len(source_ids) != len(set(source_ids))
            or not all(isinstance(source_id, str) for source_id in source_ids)
        ):
            raise ValueError("Calculation planner returned invalid operand fields.")
        try:
            normalized_number = parse_evidence_number(numeric_value)
            quoted_number = parse_evidence_number(verbatim)
        except CalculationError as error:
            raise ValueError("Calculation planner returned a non-numeric operand.") from error
        if normalized_number != quoted_number:
            raise ValueError("Calculation operand does not match its verbatim evidence value.")
        for source_id in source_ids:
            source = evidence_by_id.get(source_id)
            if source is None or verbatim not in source.get("text", ""):
                raise ValueError("Calculation operand is not present in its cited evidence.")
            if require_periods:
                period = item["period"]
                source_text = source.get("text", "")
                if not isinstance(period, str) or not period.strip() or len(period) > 80 or period not in source_text:
                    raise ValueError("Calculation period is not present in its cited evidence.")
                if unit is None:
                    raise ValueError("Evidence calculations require disclosed operand units.")
                # Currency symbols and ordinary singular/plural scale labels
                # are equivalent; magnitudes are never converted here.
                def unit_terms(text):
                    text = text.lower().replace("$", " usd ").replace("€", " eur ")
                    return {word.rstrip("s") for word in re.findall(r"[a-z]+", text)}

                if not unit_terms(unit) <= unit_terms(source_text):
                    raise ValueError("Calculation unit is not present in its cited evidence.")
        operands.append(
            EvidenceOperand(
                label.strip(),
                format(normalized_number, "f"),
                verbatim,
                unit.strip() if isinstance(unit, str) else None,
                tuple(source_ids),
            )
        )

    operand_units = {operand.unit for operand in operands}
    if expected_operation in {"difference", "sum"}:
        nonempty_units = {unit for unit in operand_units if unit is not None}
        if len(nonempty_units) > 1 or (
            nonempty_units and result_unit not in nonempty_units
        ):
            raise ValueError("Calculation operands have incompatible additive units.")
    elif len(operand_units) > 1:
        raise ValueError("Calculation operands have incompatible comparative units.")
    if expected_operation in {"percentage", "growth_rate"} and result_unit != "%":
        raise ValueError("Percentage calculations must return a percent unit.")
    return EvidenceCalculationPlan(
        "ready",
        expected_operation,
        tuple(operands),
        result_unit.strip() if isinstance(result_unit, str) else None,
        decimal_places,
        None,
    )
