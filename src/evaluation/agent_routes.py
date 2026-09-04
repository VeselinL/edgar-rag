"""Evaluate the reviewed finalization route/tool manifest without changing P0 data."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

from src.generation.rag import GenerationService, make_llm_client
from src.orchestration.routing import RequestRoute
from src.resolution.companies import default_company_resolver


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT_ROOT / "data/evaluation/finalization/v1/agent_routes.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/evaluation/finalization/v1/phase2/agent_routes.json"
_CATEGORY_COUNTS = {
    "filing_no_tool": 10, "web_required": 10, "web_false_positive": 10,
    "calculator_required": 10, "calculator_false_positive": 10,
    "upload": 5, "conversation_memory": 5,
}


def load_agent_routes(path: Path = DEFAULT_MANIFEST) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    required = {"case_id", "category", "query", "expected_route", "expected_tickers", "expected_tool_sequence"}
    if len(cases) != 60 or Counter(case.get("category") for case in cases) != _CATEGORY_COUNTS:
        raise ValueError("Agent route manifest has an invalid category distribution.")
    if len({case.get("case_id") for case in cases}) != len(cases):
        raise ValueError("Agent route manifest case IDs must be unique.")
    for case in cases:
        if not isinstance(case, dict) or not required <= set(case) or not isinstance(case["query"], str):
            raise ValueError("Agent route manifest contains an invalid case.")
        if not all(isinstance(value, str) for value in case["expected_tickers"] + case["expected_tool_sequence"]):
            raise ValueError("Agent route manifest contains invalid expected values.")
    return cases


def _tools(route: RequestRoute) -> list[str]:
    tools: list[str] = []
    if route.uses_filing_retrieval:
        tools.append("filing_retrieval")
    if route.uses_uploads:
        tools.append("upload_retrieval")
    if route.uses_web_search:
        tools.append("web_search")
    if route.uses_calculator:
        tools.append("calculator")
    return tools


def evaluate_agent_routes(cases: list[dict[str, Any]], router: Any) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        resolution = default_company_resolver.resolve(case["query"])
        try:
            route = router.route_request(
                case["query"], resolution, "", case.get("uploads", ())
            )
            actual_tickers = list(
                route.resolved_tickers
                or (resolution.resolved_tickers if _tools(route) else ())
            )
            route_match = route.route.value == case["expected_route"]
            ticker_match = actual_tickers == case["expected_tickers"]
            tool_match = _tools(route) == case["expected_tool_sequence"]
            freshness_match = "freshness" not in case or case["freshness"] == route.freshness.value
            source_key_match = "web_source_keys" not in case or case["web_source_keys"] == [key.value for key in route.web_source_keys]
            records.append({
                "case_id": case["case_id"], "category": case["category"],
                "expected_route": case["expected_route"], "actual_route": route.route.value,
                "expected_tickers": case["expected_tickers"], "actual_tickers": actual_tickers,
                "expected_tool_sequence": case["expected_tool_sequence"], "actual_tool_sequence": _tools(route),
                "route_match": route_match, "ticker_match": ticker_match,
                "tool_match": tool_match, "freshness_match": freshness_match,
                "source_key_match": source_key_match,
                "pass": all((route_match, ticker_match, tool_match, freshness_match, source_key_match)),
                "latency_ms": round((time.perf_counter() - started) * 1_000, 3), "error": None,
            })
        except Exception as error:
            records.append({"case_id": case["case_id"], "category": case["category"], "pass": False, "error": {"type": type(error).__name__}, "latency_ms": round((time.perf_counter() - started) * 1_000, 3)})
    count = len(records)
    web_required = [record for record in records if record["category"] == "web_required"]
    web_traps = [record for record in records if record["category"] == "web_false_positive"]
    calculator_traps = [record for record in records if record["category"] == "calculator_false_positive"]
    summary = {
        "case_count": count, "passed_count": sum(record["pass"] for record in records),
        "route_accuracy": sum(record.get("route_match", False) for record in records) / count,
        "web_required_recall": sum(record.get("actual_route") == "web" for record in web_required) / len(web_required) if web_required else 1.0,
        "unnecessary_web_call_rate": sum(record.get("actual_route") == "web" for record in web_traps) / len(web_traps) if web_traps else 0.0,
        "calculator_false_positives": sum("calculator" in record.get("actual_tool_sequence", []) for record in calculator_traps),
        "gate_pass": all(record["pass"] for record in records),
    }
    return {"summary": summary, "records": records}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = evaluate_agent_routes(load_agent_routes(args.manifest), GenerationService(make_llm_client()))
    output = {"schema_version": 1, "evaluated_at": datetime.now(timezone.utc).isoformat(), **result}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output["summary"], indent=2))
    if not output["summary"]["gate_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
