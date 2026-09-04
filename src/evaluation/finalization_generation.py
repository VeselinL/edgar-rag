"""Run frozen oracle-context generation measurements for Phase 4."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Any, Sequence

from src.evaluation.finalization_baseline import DEFAULT_CASES, load_cases
from src.evaluation.freeze import DEFAULT_MANIFEST as DEFAULT_FREEZE_MANIFEST, validate_manifest
from src.evaluation.generation_quality import load_chunk_lookup
from src.generation.rag import GenerationService, make_llm_client, resolve_cited_evidence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ABSTENTION = re.compile(r"(?:not provide enough|insufficient|cannot answer|can't answer|not enough evidence|no relevant evidence)", re.IGNORECASE)


def evaluate_oracle_context(
    cases: Sequence[dict[str, Any]], chunks: dict[str, dict[str, Any]], service: Any
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for case in cases:
        evidence = [{"chunk": chunks[chunk_id]} for chunk_id in case["gold_chunk_ids"]]
        started = time.perf_counter()
        try:
            answer = service.answer_with_metadata(case["query"], evidence)
            citations = resolve_cited_evidence(answer.text, evidence)
            records.append({
                "case_id": case["case_id"], "category": case["category"],
                "query": case["query"], "gold_chunk_ids": case["gold_chunk_ids"],
                "answer": answer.text, "provider_usage": answer.usage,
                "parsed_citation_ids": list(citations.parsed_ids),
                "resolved_citation_ids": list(citations.resolved_ids),
                "rejected_citation_ids": list(citations.rejected_ids),
                "expects_abstention": case["expects_abstention"],
                "abstained": bool(ABSTENTION.search(answer.text)),
                "citation_exact": not citations.rejected_ids,
                "latency_ms": round((time.perf_counter() - started) * 1_000, 3),
                "error": None,
            })
        except Exception as error:
            records.append({
                "case_id": case["case_id"], "category": case["category"],
                "error": {"type": type(error).__name__},
                "latency_ms": round((time.perf_counter() - started) * 1_000, 3),
            })
    return {"schema_version": 1, "layer": "generator_oracle_context", "evaluated_at": datetime.now(timezone.utc).isoformat(), "records": records}


def write_run(result: dict[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    records = result["records"]
    (directory / "raw.jsonl").write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    (directory / "summary.json").write_text(json.dumps({key: value for key, value in result.items() if key != "records"}, indent=2) + "\n", encoding="utf-8")
    (directory / "failures.jsonl").write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records if record.get("error") or not record.get("citation_exact", False) or record.get("abstained") != record.get("expects_abstention")), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--stop", type=int)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--freeze-manifest", type=Path, default=DEFAULT_FREEZE_MANIFEST)
    arguments = parser.parse_args()
    validate_manifest(arguments.freeze_manifest)
    cases = load_cases(DEFAULT_CASES)[arguments.start:arguments.stop]
    service = GenerationService(make_llm_client())
    write_run(evaluate_oracle_context(cases, load_chunk_lookup(), service), arguments.output_directory)


if __name__ == "__main__":
    main()
