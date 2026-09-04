"""Versioned, shared generation and citation quality evaluation for AVA."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import re
import statistics
import time
from typing import Any

from src.filings.corpus import ACTIVE_FILINGS
from src.generation.rag import (
    DEFAULT_LLM_MODEL,
    GenerationService,
    citation_ids,
    format_context,
    make_llm_client,
    resolve_cited_evidence,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = PROJECT_ROOT / "data/evaluation/ava_p0/v1/generation_quality_v1.json"
ABSTENTION_PATTERN = re.compile(
    r"(?:not provide enough|does not provide any information|insufficient|cannot answer|can't answer|not enough evidence|no (?:relevant )?evidence (?:was )?found)",
    re.IGNORECASE,
)


def load_generation_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("cases"), list):
        raise ValueError("Unsupported generation-quality case schema.")
    identifiers = [case.get("id") for case in payload["cases"]]
    if any(not isinstance(value, str) or not value for value in identifiers):
        raise ValueError("Every generation-quality case needs an ID.")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Generation-quality case IDs must be unique.")
    return payload["cases"]


def load_chunk_lookup() -> dict[str, dict[str, Any]]:
    chunks: dict[str, dict[str, Any]] = {}
    for ticker, filing in ACTIVE_FILINGS.items():
        path = PROJECT_ROOT / "data/chunks" / ticker / f"{filing}.chunks.jsonl"
        with path.open(encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    chunk = json.loads(line)
                    chunks[chunk["chunk_id"]] = chunk
    return chunks


def _claim_match(answer: str, claim: dict[str, Any]) -> bool:
    folded = answer.casefold()
    return all(str(term).casefold() in folded for term in claim["terms"])


def score_generation_answer(
    case: dict[str, Any], answer: str, evidence: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Score reviewed facts and exact citations without asking an LLM to infer sources."""
    resolution = resolve_cited_evidence(answer, evidence)
    parsed = set(resolution.parsed_ids)
    matched = [claim for claim in case["required_claims"] if _claim_match(answer, claim)]
    supported = [
        claim
        for claim in matched
        if parsed.intersection(claim["support_ids"])
    ]
    numerical = [claim for claim in case["required_claims"] if claim["numerical"]]
    correct_numerical = [claim for claim in numerical if _claim_match(answer, claim)]
    comparison_companies = case["comparison_companies"]
    covered_companies = [
        company for company in comparison_companies if company.casefold() in answer.casefold()
    ]
    contradiction_count = sum(
        term.casefold() in answer.casefold() for term in case["contradiction_terms"]
    )
    abstained = bool(ABSTENTION_PATTERN.search(answer))
    required_count = len(case["required_claims"])
    cited_claim_count = len(supported)
    uncited_fact_count = len(matched) - cited_claim_count
    parsed_count = len(resolution.parsed_ids)
    return {
        "required_fact_count": required_count,
        "covered_required_fact_count": len(matched),
        "supported_labeled_claim_count": cited_claim_count,
        "uncited_labeled_fact_count": uncited_fact_count,
        "numerical_fact_count": len(numerical),
        "correct_numerical_fact_count": len(correct_numerical),
        "comparison_company_count": len(comparison_companies),
        "covered_comparison_company_count": len(covered_companies),
        "expects_abstention": case["expects_abstention"],
        "abstained": abstained,
        "abstention_correct": abstained == case["expects_abstention"],
        "contradiction_count": contradiction_count,
        "parsed_citation_ids": list(resolution.parsed_ids),
        "resolved_citation_ids": list(resolution.resolved_ids),
        "rejected_citation_ids": list(resolution.rejected_ids),
        "citation_precision": (
            len(resolution.resolved_ids) / parsed_count if parsed_count else (1.0 if abstained else 0.0)
        ),
        "citation_recall": (
            cited_claim_count / required_count if required_count else 1.0
        ),
        "source_display_exact": not resolution.rejected_ids,
    }


Judge = Callable[[dict[str, Any], str, Sequence[dict[str, Any]]], dict[str, Any]]


def provider_grounding_judge(client: Any, model: str = DEFAULT_LLM_MODEL) -> Judge:
    fields = {
        "factual_claim_count", "supported_claim_count", "unsupported_claim_count",
        "contradiction_count", "uncited_factual_claim_count",
    }

    def judge(case: dict[str, Any], answer: str, evidence: Sequence[dict[str, Any]]) -> dict[str, Any]:
        prompt = {
            "question": case["query"],
            "answer": answer,
            "reviewed_required_facts": case["required_claims"],
            "expects_abstention": case["expects_abstention"],
            "evidence": format_context(evidence),
        }
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": (
                    "Audit every atomic factual claim, including conclusions, against only the "
                    "provided evidence. Count a claim as supported only when entailed. Count factual "
                    "claims without an exact supporting inline source ID as uncited. Return JSON only "
                    "with integer factual_claim_count, supported_claim_count, unsupported_claim_count, "
                    "contradiction_count, and uncited_factual_claim_count."
                )},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.0,
            max_tokens=1_000,
        )
        raw = (response.choices[0].message.content or "").strip()
        raw = raw.removeprefix("```json").removesuffix("```").strip()
        value = json.loads(raw)
        if set(value) != fields or any(not isinstance(value[key], int) for key in fields):
            raise ValueError("Grounding judge returned an invalid metric contract.")
        return value

    return judge


def evaluate_generation_quality(
    cases: Sequence[dict[str, Any]],
    chunks: dict[str, dict[str, Any]],
    *,
    answer_provider: Callable[[dict[str, Any], Sequence[dict[str, Any]]], tuple[str, dict[str, int]]] | None = None,
    judge: Judge | None = None,
    answer_mode: str = "reference",
    model: str | None = None,
) -> dict[str, Any]:
    records = []
    latencies = []
    for case in cases:
        missing = [item for item in case["final_evidence_ids"] if item not in chunks]
        if missing:
            raise ValueError(f"{case['id']} references missing chunks: {missing}")
        evidence = [{"chunk": chunks[item]} for item in case["final_evidence_ids"]]
        started = time.perf_counter()
        if answer_provider:
            answer, usage = answer_provider(case, evidence)
        else:
            answer, usage = case["reference_answer"], {}
        deterministic = score_generation_answer(case, answer, evidence)
        judgment = judge(case, answer, evidence) if judge else None
        latency_ms = (time.perf_counter() - started) * 1_000
        latencies.append(latency_ms)
        records.append({
            "id": case["id"], "category": case["category"], "query": case["query"],
            "answer": answer, "final_evidence_ids": case["final_evidence_ids"],
            "deterministic": deterministic, "grounding_judge": judgment,
            "provider_usage": usage, "latency_ms": latency_ms,
        })
    deterministic = [record["deterministic"] for record in records]
    judged = [record["grounding_judge"] for record in records if record["grounding_judge"]]
    judged_non_abstention = [
        record["grounding_judge"]
        for record in records
        if record["grounding_judge"] and not record["deterministic"]["expects_abstention"]
    ]
    required = sum(item["required_fact_count"] for item in deterministic)
    numerical = sum(item["numerical_fact_count"] for item in deterministic)
    comparison = sum(item["comparison_company_count"] for item in deterministic)
    factual = sum(item["factual_claim_count"] for item in judged) if judged else 0
    judged_classified = sum(
        item["supported_claim_count"] + item["unsupported_claim_count"] for item in judged
    )
    non_abstention_factual = sum(
        item["factual_claim_count"] for item in judged_non_abstention
    )
    fingerprint = hashlib.sha256()
    for chunk_id in sorted(chunks):
        fingerprint.update(chunk_id.encode())
        fingerprint.update(b"\n")
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "answer_mode": answer_mode,
        "model": model,
        "corpus_chunk_id_fingerprint": "sha256:" + fingerprint.hexdigest(),
        "summary": {
            "case_count": len(records),
            "category_counts": dict(sorted(Counter(record["category"] for record in records).items())),
            "completeness": sum(item["covered_required_fact_count"] for item in deterministic) / required if required else 1.0,
            "labeled_claim_support": sum(item["supported_labeled_claim_count"] for item in deterministic) / required if required else 1.0,
            "numerical_correctness": sum(item["correct_numerical_fact_count"] for item in deterministic) / numerical if numerical else 1.0,
            "abstention_accuracy": statistics.fmean(float(item["abstention_correct"]) for item in deterministic),
            "comparison_coverage": sum(item["covered_comparison_company_count"] for item in deterministic) / comparison if comparison else 1.0,
            "contradiction_rate": sum(item["contradiction_count"] for item in deterministic) / max(required, 1),
            "citation_precision": statistics.fmean(item["citation_precision"] for item in deterministic),
            "citation_recall": statistics.fmean(item["citation_recall"] for item in deterministic),
            "invalid_citation_count": sum(len(item["rejected_citation_ids"]) for item in deterministic),
            "uncited_labeled_fact_count": sum(item["uncited_labeled_fact_count"] for item in deterministic),
            "source_display_exactness": statistics.fmean(float(item["source_display_exact"]) for item in deterministic),
            "judge_claim_support": sum(item["supported_claim_count"] for item in judged) / judged_classified if judged_classified else None,
            "judge_unsupported_claim_rate": sum(item["unsupported_claim_count"] for item in judged) / judged_classified if judged_classified else None,
            "judge_uncited_factual_claim_rate": sum(item["uncited_factual_claim_count"] for item in judged_non_abstention) / non_abstention_factual if non_abstention_factual else None,
            "latency_ms": {"mean": statistics.fmean(latencies), "max": max(latencies)},
        },
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate AVA generation and citations separately from retrieval.")
    parser.add_argument("--answers", choices=("reference", "provider"), default="reference")
    parser.add_argument("--judge-provider", action="store_true")
    parser.add_argument("--model", default=DEFAULT_LLM_MODEL)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    client = make_llm_client() if args.answers == "provider" or args.judge_provider else None
    service = (
        GenerationService(
            client,
            model=args.model,
        )
        if args.answers == "provider"
        else None
    )

    def answer_provider(case: dict[str, Any], evidence: Sequence[dict[str, Any]]) -> tuple[str, dict[str, int]]:
        assert service is not None
        result = service.answer_with_metadata(case["query"], evidence)
        return result.text, result.usage

    result = evaluate_generation_quality(
        load_generation_cases(), load_chunk_lookup(),
        answer_provider=answer_provider if service else None,
        judge=provider_grounding_judge(client, args.model) if args.judge_provider else None,
        answer_mode=args.answers,
        model=args.model if client else None,
    )
    result["prompt_version"] = service.prompt_version if service else None
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
