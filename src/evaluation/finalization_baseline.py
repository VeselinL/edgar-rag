"""Run the frozen Phase 4 retriever-only AVA baseline."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time
from typing import Any, Sequence

from src.evaluation.ava_p0 import build_retriever
from src.evaluation.freeze import DEFAULT_MANIFEST as DEFAULT_FREEZE_MANIFEST, validate_manifest
from src.resolution.companies import default_company_resolver


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = PROJECT_ROOT / "data/evaluation/finalization/v1/qa_gold.jsonl"
DEFAULT_RUN_DIRECTORY = PROJECT_ROOT / "data/evaluation/finalization/v1/runs/retriever-only-v1"


def load_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if len(cases) != 75 or len({case.get("case_id") for case in cases}) != 75:
        raise ValueError("The finalization QA manifest must contain 75 unique cases.")
    required = {
        "case_id", "category", "query", "expected_tickers", "gold_chunk_ids",
        "expects_abstention",
    }
    if any(not required <= set(case) for case in cases):
        raise ValueError("The finalization QA manifest has an invalid record.")
    return cases


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def evaluate_retriever_only(cases: Sequence[dict[str, Any]], retriever: Any) -> dict[str, Any]:
    """Measure fixed-query candidate retrieval and final evidence selection."""
    records: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        resolution = default_company_resolver.resolve(case["query"])
        try:
            outcome = retriever.retrieve(case["query"], company_resolution=resolution)
            candidate_ids = [item["chunk"]["chunk_id"] for item in outcome.candidates]
            final_ids = list(outcome.chunk_ids)
            gold_ids = list(case["gold_chunk_ids"])
            candidate_gold = [item for item in gold_ids if item in candidate_ids]
            final_gold = [item for item in gold_ids if item in final_ids]
            first_rank = min((candidate_ids.index(item) + 1 for item in candidate_gold), default=None)
            records.append({
                "case_id": case["case_id"], "category": case["category"],
                "query": case["query"], "expected_tickers": case["expected_tickers"],
                "detected_tickers": list(outcome.detected_companies),
                "retrieval_scopes": list(outcome.retrieval_scopes),
                "candidate_chunk_ids": candidate_ids, "final_chunk_ids": final_ids,
                "gold_chunk_ids": gold_ids,
                "scope_exact": list(outcome.detected_companies) == case["expected_tickers"],
                "candidate_recall": len(candidate_gold) / len(gold_ids) if gold_ids else None,
                "gold_survival": len(final_gold) / len(candidate_gold) if candidate_gold else None,
                "hit_at_50": bool(candidate_gold) if gold_ids else None,
                "mrr_at_50": 1 / first_rank if first_rank else (0.0 if gold_ids else None),
                "latency_ms": round((time.perf_counter() - started) * 1_000, 3),
                "error": None,
            })
        except Exception as error:
            records.append({
                "case_id": case["case_id"], "category": case["category"],
                "query": case["query"], "error": {"type": type(error).__name__},
                "latency_ms": round((time.perf_counter() - started) * 1_000, 3),
            })
    scored = [record for record in records if record.get("candidate_recall") is not None]
    return {
        "schema_version": 1,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "layer": "retriever_only",
        "summary": {
            "case_count": len(records), "scored_case_count": len(scored),
            "error_count": sum(record.get("error") is not None for record in records),
            "scope_accuracy": _mean([float(record["scope_exact"]) for record in records if "scope_exact" in record]),
            "candidate_recall": _mean([record["candidate_recall"] for record in scored]),
            "gold_survival": _mean([record["gold_survival"] for record in scored if record["gold_survival"] is not None]),
            "hit_at_50": _mean([float(record["hit_at_50"]) for record in scored]),
            "mrr_at_50": _mean([record["mrr_at_50"] for record in scored]),
            "latency_ms": {"mean": _mean([record["latency_ms"] for record in records]), "max": max((record["latency_ms"] for record in records), default=0.0)},
            "category_counts": dict(sorted(Counter(record["category"] for record in records).items())),
        },
        "records": records,
    }


def write_run(result: dict[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    records = result["records"]
    (directory / "raw.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    (directory / "summary.json").write_text(
        json.dumps({key: value for key, value in result.items() if key != "records"}, indent=2) + "\n",
        encoding="utf-8",
    )
    (directory / "failures.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records if record.get("error") or record.get("candidate_recall", 1.0) < 1.0),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_RUN_DIRECTORY)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--stop", type=int)
    parser.add_argument("--freeze-manifest", type=Path, default=DEFAULT_FREEZE_MANIFEST)
    arguments = parser.parse_args()
    validate_manifest(arguments.freeze_manifest)
    cases = load_cases(arguments.cases)[arguments.start:arguments.stop]
    result = evaluate_retriever_only(cases, build_retriever(arguments.device))
    write_run(result, arguments.output_directory)
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
