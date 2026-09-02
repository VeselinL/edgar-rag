"""Evaluate AVA's deterministic calculator against labeled safe expressions."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Sequence

from src.tools.calculator import CalculatorTool


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT_ROOT / "data/evaluation/ava_p0/v1/calculator_regressions_v1.json"
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "data/evaluation/ava_p0/v1/runs/calculator-regressions.json"
)


def load_calculator_manifest(path: Path = DEFAULT_MANIFEST) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if payload.get("schema_version") != 1 or not isinstance(cases, list):
        raise ValueError("Unsupported calculator manifest schema.")
    required = {"id", "query", "result", "unit"}
    identifiers: list[str] = []
    for case in cases:
        if not isinstance(case, dict) or set(case) != required:
            raise ValueError("Calculator cases must use the exact supported fields.")
        if not all(isinstance(case[key], str) and case[key] for key in ("id", "query", "result")):
            raise ValueError("Calculator case ID, query, and result must be non-empty strings.")
        if case["unit"] is not None and not isinstance(case["unit"], str):
            raise ValueError("Calculator case unit must be a string or null.")
        identifiers.append(case["id"])
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Calculator case IDs must be unique.")
    return cases


def evaluate_calculator(
    cases: Sequence[dict[str, Any]], calculator: CalculatorTool | None = None
) -> dict[str, Any]:
    tool = calculator or CalculatorTool()
    records: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        try:
            actual = tool.calculate_query(case["query"])
            passed = actual.result == case["result"] and actual.unit == case["unit"]
            records.append(
                {
                    "case_id": case["id"],
                    "expected_result": case["result"],
                    "actual_result": actual.result,
                    "expected_unit": case["unit"],
                    "actual_unit": actual.unit,
                    "normalized_expression": actual.normalized_expression,
                    "latency_ms": round((time.perf_counter() - started) * 1_000, 3),
                    "pass": passed,
                    "error": None,
                }
            )
        except Exception as error:
            records.append(
                {
                    "case_id": case["id"],
                    "expected_result": case["result"],
                    "actual_result": None,
                    "expected_unit": case["unit"],
                    "actual_unit": None,
                    "normalized_expression": None,
                    "latency_ms": round((time.perf_counter() - started) * 1_000, 3),
                    "pass": False,
                    "error": {"type": type(error).__name__, "message": str(error)},
                }
            )
    passed_count = sum(record["pass"] for record in records)
    latencies = [record["latency_ms"] for record in records]
    return {
        "summary": {
            "case_count": len(records),
            "passed_count": passed_count,
            "exact_accuracy": passed_count / len(records) if records else 1.0,
            "error_count": sum(record["error"] is not None for record in records),
            "latency_ms": {
                "mean": sum(latencies) / len(latencies) if latencies else 0.0,
                "max": max(latencies, default=0.0),
            },
            "gate_pass": passed_count == len(records),
        },
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    output = {
        "schema_version": 1,
        "evaluation": "calculator_quality",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        **evaluate_calculator(load_calculator_manifest(arguments.manifest)),
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output["summary"], indent=2))
    if not output["summary"]["gate_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
