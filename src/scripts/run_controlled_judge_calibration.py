"""Build and score a controlled calibration packet for AVA's diagnostic LLM judge.

The packet uses frozen filing chunks and labels intentional answer perturbations
before any provider request is made.  The provider receives no gold labels.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from src.config.settings import DEFAULT_LLM_MODEL
from src.generation.provider import make_llm_client, provider_usage


FIELDS = (
    "claim_correctness",
    "faithfulness",
    "citation_support",
    "abstention",
    "answer_relevance",
    "conciseness",
)
CHUNK_FILES = Path("data/chunks")

SEEDS = (
    {
        "name": "aurora-mission",
        "chunk_id": "AUR-2025-CHUNK-000002",
        "question": "What is Aurora's mission for delivering self-driving technology?",
        "answer": "Aurora's mission is to deliver the benefits of self-driving technology safely, quickly, and broadly [AUR-2025-CHUNK-000002].",
        "incorrect": "Aurora's mission is to deliver the benefits of self-driving technology safely, slowly, and narrowly [AUR-2025-CHUNK-000002].",
        "irrelevant": "Aurora was founded in 2017 by Chris Urmson, Sterling Anderson, and Drew Bagnell [AUR-2025-CHUNK-000002].",
        "absent_question": "What cash dividend per share did Aurora declare in 2025?",
    },
    {
        "name": "tesla-segments",
        "chunk_id": "TSLA-2025-CHUNK-000002",
        "question": "What are Tesla's reportable segments?",
        "answer": "Tesla operates two reportable segments: automotive and energy generation and storage [TSLA-2025-CHUNK-000002].",
        "incorrect": "Tesla operates three reportable segments: automotive, energy generation and storage, and insurance [TSLA-2025-CHUNK-000002].",
        "irrelevant": "Tesla's automotive segment includes vehicle sales and leasing [TSLA-2025-CHUNK-000002].",
        "absent_question": "What cash dividend per share did Tesla declare in 2025?",
    },
    {
        "name": "ford-officers",
        "chunk_id": "F-2025-CHUNK-000123",
        "question": "Who were Ford's CEO and COO, and when did each begin the role?",
        "answer": "Ford's CEO was James D. Farley, Jr. from October 2020, and its COO was Ashwani (Kumar) Galhotra from October 2023 [F-2025-CHUNK-000123].",
        "incorrect": "Ford's CEO was James D. Farley, Jr. from October 2022, and its COO was Ashwani (Kumar) Galhotra from October 2023 [F-2025-CHUNK-000123].",
        "irrelevant": "William Clay Ford, Jr. was Ford's Executive Chair and Chair of the Board from September 2006 [F-2025-CHUNK-000123].",
        "absent_question": "What cash dividend per share did Ford declare in 2025?",
    },
    {
        "name": "tesla-revenue",
        "chunk_id": "TSLA-2025-CHUNK-000121",
        "question": "By how much did Tesla's total revenues change from 2024 to 2025?",
        "answer": "Tesla's total revenues decreased by $2,863 million, or 3%, from 2024 to 2025 [TSLA-2025-CHUNK-000121].",
        "incorrect": "Tesla's total revenues increased by $2,863 million, or 3%, from 2024 to 2025 [TSLA-2025-CHUNK-000121].",
        "irrelevant": "Tesla's energy generation and storage segment revenue increased by $2,685 million in 2025 versus 2024 [TSLA-2025-CHUNK-000121].",
        "absent_question": "What cash dividend per share did Tesla declare in 2025?",
    },
)


def _load_chunks() -> dict[str, str]:
    wanted = {seed["chunk_id"] for seed in SEEDS}
    found: dict[str, str] = {}
    for path in CHUNK_FILES.rglob("*.chunks.jsonl"):
        for line in path.open(encoding="utf-8"):
            record = json.loads(line)
            chunk_id = record.get("chunk_id")
            if chunk_id in wanted:
                found[chunk_id] = record["text"]
    if missing := wanted - found.keys():
        raise RuntimeError(f"Controlled calibration chunks are missing: {sorted(missing)}")
    return found


def _rubric(**fails: bool) -> dict[str, str]:
    return {field: "fail" if fails.get(field, False) else "pass" for field in FIELDS}


def build_packet() -> dict[str, Any]:
    chunks = _load_chunks()
    pairs: list[dict[str, Any]] = []
    for seed in SEEDS:
        evidence = [{"chunk_id": seed["chunk_id"], "text": chunks[seed["chunk_id"]]}]
        answer = seed["answer"]
        no_evidence = "No qualifying evidence was provided. I cannot determine that from the available materials."
        variants = (
            ("grounded_control", seed["question"], evidence, answer, answer, _rubric()),
            ("incorrect_claim", seed["question"], evidence, answer, seed["incorrect"], _rubric(claim_correctness=True, faithfulness=True, citation_support=True)),
            ("false_abstention", seed["question"], evidence, answer, no_evidence, _rubric(claim_correctness=True, faithfulness=True, citation_support=True, abstention=True, answer_relevance=True)),
            ("irrelevant_supported", seed["question"], evidence, answer, seed["irrelevant"], _rubric(answer_relevance=True)),
            ("redundant_grounded", seed["question"], evidence, answer, " ".join((answer, answer, answer)), _rubric(conciseness=True)),
            ("proper_abstention", seed["absent_question"], [], no_evidence, no_evidence, _rubric()),
        )
        for variant, question, item_evidence, answer_a, answer_b, rubric in variants:
            pairs.append({
                "pair_id": f"controlled-{seed['name']}-{variant}",
                "seed": seed["name"],
                "variant": variant,
                "question": question,
                "evidence": item_evidence,
                "answer_a": answer_a,
                "answer_b": answer_b,
                "authoritative_rubric": rubric,
            })
    return {
        "schema_version": 1,
        "review_method": "controlled perturbations labeled before diagnostic LLM evaluation",
        "fields": list(FIELDS),
        "pair_count": len(pairs),
        "pairs": pairs,
    }


def _judge_prompt(pair: dict[str, Any]) -> list[dict[str, str]]:
    evidence = "\n\n".join(
        f"[{item['chunk_id']}]\n{item['text']}" for item in pair["evidence"]
    ) or "(No evidence was supplied.)"
    return [
        {"role": "system", "content": "You are an independent diagnostic evaluator. Return only one JSON object; do not explain your reasoning."},
        {"role": "user", "content": f"""Evaluate BOTH candidate answers against the question and supplied evidence. A pair passes a field only if both answers pass it. Treat a factual claim without supporting supplied evidence as unsupported. For abstention, pass when an answer is given from supplied evidence or when it properly refuses because the supplied evidence cannot answer; fail only when it refuses despite answerable evidence or answers without sufficient evidence.

Question: {pair['question']}

Evidence:\n{evidence}

Answer A: {pair['answer_a']}

Answer B: {pair['answer_b']}

Return exactly this JSON shape, using only \"pass\" or \"fail\" values:
{{"claim_correctness":"pass|fail","faithfulness":"pass|fail","citation_support":"pass|fail","abstention":"pass|fail","answer_relevance":"pass|fail","conciseness":"pass|fail"}}"""},
    ]


def _parse_rubric(content: str) -> dict[str, str]:
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Judge did not return a JSON object.")
    parsed = json.loads(content[start : end + 1])
    if set(parsed) != set(FIELDS) or any(value not in {"pass", "fail"} for value in parsed.values()):
        raise ValueError("Judge returned an invalid rubric contract.")
    return {field: parsed[field] for field in FIELDS}


def _cohen_kappa(left: list[str], right: list[str]) -> float | None:
    if not left:
        return None
    observed = sum(a == b for a, b in zip(left, right, strict=True)) / len(left)
    expected = sum(
        (left.count(label) / len(left)) * (right.count(label) / len(right))
        for label in ("pass", "fail")
    )
    return None if expected == 1 else (observed - expected) / (1 - expected)


def judge_packet(packet: dict[str, Any], model: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    client = make_llm_client()
    records: list[dict[str, Any]] = []
    for pair in packet["pairs"]:
        try:
            response = client.chat.completions.create(
                model=model, messages=_judge_prompt(pair), temperature=0, max_tokens=180,
            )
            content = response.choices[0].message.content or ""
            judgment = _parse_rubric(content)
            error = None
        except Exception as exc:  # Provider text stays out of artifacts.
            judgment = None
            error = type(exc).__name__
            response = None
        records.append({
            "pair_id": pair["pair_id"],
            "variant": pair["variant"],
            "authoritative_rubric": pair["authoritative_rubric"],
            "llm_rubric": judgment,
            "provider_usage": provider_usage(getattr(response, "usage", None)),
            "error": error,
        })

    complete = [record for record in records if record["llm_rubric"]]
    per_field: dict[str, Any] = {}
    for field in FIELDS:
        expected = [record["authoritative_rubric"][field] for record in complete]
        observed = [record["llm_rubric"][field] for record in complete]
        counts = Counter(expected)
        confusion = {
            "true_pass": sum(a == b == "pass" for a, b in zip(expected, observed, strict=True)),
            "true_fail": sum(a == b == "fail" for a, b in zip(expected, observed, strict=True)),
            "false_pass": sum(a == "fail" and b == "pass" for a, b in zip(expected, observed, strict=True)),
            "false_fail": sum(a == "pass" and b == "fail" for a, b in zip(expected, observed, strict=True)),
        }
        per_field[field] = {
            "agreement": sum(a == b for a, b in zip(expected, observed, strict=True)) / len(expected) if expected else None,
            "cohen_kappa": _cohen_kappa(expected, observed),
            "authoritative": dict(counts),
            "llm": dict(Counter(observed)),
            "confusion": confusion,
            "failure_recall": confusion["true_fail"] / counts["fail"] if counts["fail"] else None,
            "false_pass_rate": confusion["false_pass"] / counts["fail"] if counts["fail"] else None,
        }
    decisions = len(complete) * len(FIELDS)
    agreement = sum(
        record["authoritative_rubric"][field] == record["llm_rubric"][field]
        for record in complete for field in FIELDS
    ) / decisions if decisions else None
    return records, {
        "schema_version": 1,
        "review_method": packet["review_method"],
        "model": model,
        "temperature": 0.0,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "pair_count": len(packet["pairs"]),
        "completed_count": len(complete),
        "error_count": len(packet["pairs"]) - len(complete),
        "aggregate_agreement": agreement,
        "per_field": per_field,
        "limitations": [
            "The authoritative labels are controlled perturbation labels recorded by the evaluator, not an independent second-human review.",
            "This tests obvious, source-bounded failures; it does not certify nuanced financial interpretation, multi-hop synthesis, or production-answer quality.",
            "The diagnostic LLM remains non-authoritative and must not grade its own generated answer.",
        ],
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_LLM_MODEL)
    args = parser.parse_args()
    packet = build_packet()
    packet["packet_sha256"] = hashlib.sha256(
        json.dumps(packet["pairs"], sort_keys=True).encode("utf-8")
    ).hexdigest()
    _write_json(args.output_dir / "blinded_pairs.json", packet)
    records, summary = judge_packet(packet, args.model)
    with (args.output_dir / "llm_judge.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    _write_json(args.output_dir / "llm_judge_summary.json", summary)
    print(json.dumps({key: summary[key] for key in ("pair_count", "completed_count", "error_count", "aggregate_agreement")}, indent=2))


if __name__ == "__main__":
    main()
