"""Run the diagnostic rubric over frozen finalization end-to-end outputs."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from src.generation.provider import make_llm_client, provider_usage
from src.generation.service import GenerationService


FIELDS = (
    "claim_correctness",
    "faithfulness",
    "citation_support",
    "abstention",
    "answer_relevance",
    "conciseness",
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _prompt(gold: dict[str, Any], record: dict[str, Any]) -> list[dict[str, str]]:
    evidence = record.get("visible_sources", [])
    return [
        {"role": "system", "content": "You are an independent diagnostic evaluator. Return only one JSON object; do not explain your reasoning."},
        {"role": "user", "content": json.dumps({
            "instruction": (
                "Evaluate the answer only against the reviewed gold claims and supplied displayed sources. "
                "claim_correctness: material answer claims agree with gold/evidence; faithfulness: every material claim is supported by supplied sources; "
                "citation_support: displayed sources support the factual answer claims; abstention: answer or refusal is appropriate for expects_abstention; "
                "answer_relevance: directly answers the question; conciseness: has no material repetition or off-topic content. "
                "Use pass only if the field is satisfied."
            ),
            "question": gold["query"],
            "reviewed_gold_claims": gold["gold_claims"],
            "must_not_claim": gold["must_not_claim"],
            "expects_abstention": gold["expects_abstention"],
            "answer": record.get("visible_answer", ""),
            "displayed_sources": evidence,
            "required_output": {field: "pass|fail" for field in FIELDS},
        }, ensure_ascii=False)},
    ]


def _parse(content: str) -> dict[str, str]:
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Judge did not return JSON.")
    value = json.loads(content[start : end + 1])
    if set(value) != set(FIELDS) or any(item not in {"pass", "fail"} for item in value.values()):
        raise ValueError("Judge returned an invalid rubric.")
    return {field: value[field] for field in FIELDS}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--raw", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    gold = {item["case_id"]: item for item in _load_jsonl(args.gold)}
    records = [item for path in args.raw for item in _load_jsonl(path)]
    service = GenerationService(make_llm_client(), model=args.model, max_output_tokens=240)
    results = []
    for record in records:
        case = gold[record["case_id"]]
        if not record.get("visible_answer"):
            results.append({
                "case_id": record["case_id"], "run": record["run"], "category": record["category"],
                "judgment": {field: "fail" for field in FIELDS},
                "judged_by_provider": False, "safe_failure": "pipeline_error", "provider_usage": {},
            })
            continue
        try:
            response = service._create(model=args.model, messages=_prompt(case, record), temperature=0, max_tokens=240)
            judgment, failure = _parse(response.choices[0].message.content or ""), None
        except Exception as error:
            judgment, failure, response = {field: "fail" for field in FIELDS}, type(error).__name__, None
        results.append({
            "case_id": record["case_id"], "run": record["run"], "category": record["category"],
            "judgment": judgment, "judged_by_provider": failure is None,
            "safe_failure": failure, "provider_usage": provider_usage(getattr(response, "usage", None)),
        })
    complete = [item for item in results if item["judged_by_provider"]]
    summary = {
        "schema_version": 1,
        "model": args.model,
        "temperature": 0.0,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "run_count": len(args.raw),
        "record_count": len(results),
        "provider_judged_count": len(complete),
        "pipeline_or_judge_error_count": len(results) - len(complete),
        "per_field_pass_rate": {
            field: sum(item["judgment"][field] == "pass" for item in results) / len(results)
            for field in FIELDS
        },
        "category_counts": dict(sorted(Counter(item["category"] for item in results).items())),
        "limitations": [
            "The GPT-4o judge is diagnostic only; the controlled calibration found material false-pass failures.",
            "This result does not replace reviewed atomic-claim, citation, route, or tool metrics.",
            "Pipeline errors are recorded as failures without a provider judgment.",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "raw.jsonl").open("w", encoding="utf-8") as handle:
        for item in results:
            handle.write(json.dumps(item) + "\n")
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
