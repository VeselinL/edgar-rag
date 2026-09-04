"""Run frozen planner-plus-retriever Phase 4 measurements."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Sequence

from src.evaluation.ava_p0 import build_retriever
from src.evaluation.finalization_baseline import DEFAULT_CASES, load_cases
from src.evaluation.freeze import DEFAULT_MANIFEST as DEFAULT_FREEZE_MANIFEST, validate_manifest
from src.generation.rag import GenerationService, make_llm_client
from src.resolution.companies import default_company_resolver


def _history_context(history: Sequence[dict[str, str]]) -> str:
    return "\n".join(f"{item['role'].title()}: {item['content']}" for item in history)


def evaluate_planner_retriever(cases: Sequence[dict[str, Any]], planner: Any, retriever: Any) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        deterministic = default_company_resolver.resolve(case["query"])
        try:
            plan = planner.plan_retrieval(case["query"], deterministic, _history_context(case["history"]), selected_tickers=case["selected_company_scope"])
            resolution = default_company_resolver.apply_planner_resolution(deterministic, plan["company_mentions"], plan["resolved_tickers"])
            outcome = retriever.retrieve(case["query"], [item["query"] for item in plan["subqueries"]], resolution, [item["tickers"] for item in plan["subqueries"]], _history_context(case["history"]))
            candidates = [item["chunk"]["chunk_id"] for item in outcome.candidates]
            final = list(outcome.chunk_ids); gold = case["gold_chunk_ids"]
            candidate_gold = [item for item in gold if item in candidates]
            final_gold = [item for item in gold if item in final]
            records.append({"case_id":case["case_id"],"category":case["category"],"plan":plan,"expected_tickers":case["expected_tickers"],"planned_tickers":list(resolution.resolved_tickers),"candidate_chunk_ids":candidates,"final_chunk_ids":final,"gold_chunk_ids":gold,"scope_exact":list(resolution.resolved_tickers)==case["expected_tickers"],"candidate_recall":len(candidate_gold)/len(gold) if gold else None,"gold_survival":len(final_gold)/len(candidate_gold) if candidate_gold else None,"latency_ms":round((time.perf_counter()-started)*1000,3),"error":None})
        except Exception as error:
            records.append({"case_id":case["case_id"],"category":case["category"],"error":{"type":type(error).__name__},"latency_ms":round((time.perf_counter()-started)*1000,3)})
    return {"schema_version":1,"layer":"planner_retriever","evaluated_at":datetime.now(timezone.utc).isoformat(),"records":records}


def write_run(result: dict[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    records=result["records"]
    (directory/"raw.jsonl").write_text("".join(json.dumps(record,ensure_ascii=False)+"\n" for record in records),encoding="utf-8")
    (directory/"summary.json").write_text(json.dumps({key:value for key,value in result.items() if key!="records"},indent=2)+"\n",encoding="utf-8")
    (directory/"failures.jsonl").write_text("".join(json.dumps(record,ensure_ascii=False)+"\n" for record in records if record.get("error") or (record.get("candidate_recall") is not None and record["candidate_recall"]<1.0)),encoding="utf-8")


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",type=int,choices=(1,2,3),required=True)
    parser.add_argument("--start",type=int,default=0)
    parser.add_argument("--stop",type=int)
    parser.add_argument("--output-directory",type=Path,required=True)
    parser.add_argument("--freeze-manifest",type=Path,default=DEFAULT_FREEZE_MANIFEST)
    args=parser.parse_args()
    validate_manifest(args.freeze_manifest)
    result=evaluate_planner_retriever(load_cases(DEFAULT_CASES)[args.start:args.stop],GenerationService(make_llm_client()),build_retriever("cpu"))
    write_run(result,args.output_directory)


if __name__=="__main__":
    main()
