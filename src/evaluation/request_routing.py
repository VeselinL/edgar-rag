"""Evaluate AVA's frozen Phase 9 request-routing contract."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Protocol, Sequence

from src.generation.rag import DEFAULT_LLM_MODEL, GenerationService, make_llm_client
from src.orchestration.routing import RequestRoute, RouteKind
from src.resolution.companies import default_company_resolver


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT_ROOT / "data/evaluation/ava_p0/v1/request_routing_v1.json"
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "data/evaluation/ava_p0/v1/runs/phase-9-request-routing.json"
)


class RequestRouter(Protocol):
    def route_request(
        self,
        original_query: str,
        deterministic_resolution: Any = None,
        conversation_context: str = "",
        uploaded_source_names: Sequence[str] = (),
    ) -> RequestRoute: ...


def load_routing_manifest(path: Path = DEFAULT_MANIFEST) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if payload.get("schema_version") != 1 or not isinstance(cases, list):
        raise ValueError("Unsupported request-routing manifest schema.")
    identifiers = [case.get("id") for case in cases]
    if any(not isinstance(value, str) or not value for value in identifiers):
        raise ValueError("Every request-routing case needs a non-empty ID.")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Request-routing case IDs must be unique.")
    required = {
        "id",
        "query",
        "context",
        "uploads",
        "route",
        "reason_code",
        "arithmetic_required",
    }
    for case in cases:
        if set(case) != required:
            raise ValueError(f"Routing case {case.get('id')!r} has invalid fields.")
        if not isinstance(case["query"], str) or not case["query"].strip():
            raise ValueError(f"Routing case {case['id']} has an invalid query.")
        if not isinstance(case["context"], str):
            raise ValueError(f"Routing case {case['id']} has invalid context.")
        if not isinstance(case["uploads"], list) or not all(
            isinstance(name, str) and name for name in case["uploads"]
        ):
            raise ValueError(f"Routing case {case['id']} has invalid uploads.")
        RouteKind(case["route"])
        if not isinstance(case["arithmetic_required"], bool):
            raise ValueError(f"Routing case {case['id']} has invalid arithmetic label.")
    return cases


def evaluate_request_routes(
    cases: Sequence[dict[str, Any]], router: RequestRouter
) -> dict[str, Any]:
    """Score execution behavior; reason codes remain non-gating diagnostics."""
    records: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        try:
            resolution = default_company_resolver.resolve(case["query"])
            actual = router.route_request(
                case["query"],
                resolution,
                case["context"],
                case["uploads"],
            )
            route_match = actual.route.value == case["route"]
            arithmetic_match = (
                actual.arithmetic_required == case["arithmetic_required"]
            )
            records.append(
                {
                    "case_id": case["id"],
                    "expected_route": case["route"],
                    "actual_route": actual.route.value,
                    "expected_reason_code": case["reason_code"],
                    "actual_reason_code": actual.reason_code.value,
                    "expected_arithmetic_required": case["arithmetic_required"],
                    "actual_arithmetic_required": actual.arithmetic_required,
                    "decided_by": actual.decided_by,
                    "route_match": route_match,
                    "reason_match": actual.reason_code.value == case["reason_code"],
                    "arithmetic_match": arithmetic_match,
                    "pass": route_match and arithmetic_match,
                    "latency_ms": round((time.perf_counter() - started) * 1_000, 3),
                    "error": None,
                }
            )
        except Exception as error:
            records.append(
                {
                    "case_id": case["id"],
                    "expected_route": case["route"],
                    "actual_route": None,
                    "expected_reason_code": case["reason_code"],
                    "actual_reason_code": None,
                    "expected_arithmetic_required": case["arithmetic_required"],
                    "actual_arithmetic_required": None,
                    "decided_by": None,
                    "route_match": False,
                    "reason_match": False,
                    "arithmetic_match": False,
                    "pass": False,
                    "latency_ms": round((time.perf_counter() - started) * 1_000, 3),
                    "error": {"type": type(error).__name__, "message": str(error)},
                }
            )

    calculations = [
        record for record in records if record["expected_arithmetic_required"]
    ]
    non_filing = [
        record
        for record in records
        if record["expected_route"]
        not in {"filing_rag", "filing_and_calculator"}
    ]
    passed = sum(record["pass"] for record in records)
    summary = {
        "case_count": len(records),
        "passed_count": passed,
        "route_accuracy": passed / len(records) if records else 1.0,
        "reason_code_accuracy": (
            sum(record["reason_match"] for record in records) / len(records)
            if records
            else 1.0
        ),
        "calculator_required_accuracy": (
            sum(
                record["actual_arithmetic_required"] is True
                and record["actual_route"]
                in {
                    "calculator",
                    "filing_and_calculator",
                    "web_and_calculator",
                    "upload_and_calculator",
                }
                for record in calculations
            )
            / len(calculations)
            if calculations
            else 1.0
        ),
        "unnecessary_filing_route_rate": (
            sum(
                record["actual_route"] in {"filing_rag", "filing_and_calculator"}
                for record in non_filing
            )
            / len(non_filing)
            if non_filing
            else 0.0
        ),
        "error_count": sum(record["error"] is not None for record in records),
    }
    summary["gate_pass"] = bool(
        summary["route_accuracy"] == 1.0
        and summary["calculator_required_accuracy"] == 1.0
        and summary["unnecessary_filing_route_rate"] == 0.0
        and summary["error_count"] == 0
    )
    return {"summary": summary, "records": records}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default=DEFAULT_LLM_MODEL)
    arguments = parser.parse_args()

    service = GenerationService(make_llm_client(), model=arguments.model)
    result = evaluate_request_routes(load_routing_manifest(arguments.manifest), service)
    output = {
        "schema_version": 1,
        "evaluation": "phase_9_request_routing",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "model": arguments.model,
        **result,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output["summary"], indent=2))
    if not output["summary"]["gate_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
