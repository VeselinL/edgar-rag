"""Measure frozen English/Serbian route, evidence, citation, and answer parity."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from src.config.settings import ApplicationSettings
from src.conversations.context import ConversationContext
from src.evaluation.freeze import validate_manifest
from src.orchestration.executor import RealPipeline


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = PROJECT_ROOT / "data/evaluation/finalization/v1/ui_language.jsonl"
DEFAULT_MANIFEST = PROJECT_ROOT / "data/evaluation/finalization/v3/freeze_manifest.json"
PAIR_IDS = (
    "language-01", "language-02", "language-06", "language-07", "language-08",
    "language-09", "language-10", "language-11", "language-12", "language-14",
)
GOLD_CASE_IDS = {
    "language-01": "direct-aurora-mission", "language-02": "direct-tesla-segments",
    "language-06": "synthesis-ford-blue-model-e", "language-07": "direct-nvidia-platform",
    "language-08": "direct-qualcomm-business", "language-09": "direct-aptiv-footprint",
    "language-10": "direct-ouster-mission", "language-11": "direct-rivian-r1-vehicles",
    "language-12": "calculation-tsla-revenue-change", "language-14": "absent-tsla-dividend",
}
NUMBER = re.compile(r"(?<![A-Za-z0-9])[-+]?\d[\d,.]*%?")


@dataclass(frozen=True)
class LanguageContext:
    language: str

    @property
    def short_term_ids(self) -> tuple[str, ...]:
        return ()

    @property
    def long_term_ids(self) -> tuple[str, ...]:
        return ()

    def prompt_text(self) -> str:
        return ConversationContext(
            preference_text=f"Answer language: {'Serbian' if self.language == 'sr' else 'English'}."
        ).prompt_text()


def load_pairs(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    by_id = {value["case_id"]: value for value in values}
    if len(by_id) != len(values) or any(pair_id not in by_id for pair_id in PAIR_IDS):
        raise ValueError("The language parity manifest is missing a required reviewed pair.")
    return [by_id[pair_id] for pair_id in PAIR_IDS]


def load_gold() -> dict[str, dict[str, Any]]:
    values = [json.loads(line) for line in (PROJECT_ROOT / "data/evaluation/finalization/v1/qa_gold.jsonl").read_text(encoding="utf-8").splitlines() if line]
    return {value["case_id"]: value for value in values}


def _substantive_numbers(text: str) -> list[str]:
    without_list_ordinals = re.sub(r"(?m)^\s*\d+[.)]\s+", "", text)
    values: set[str] = set()
    for value in NUMBER.findall(without_list_ordinals):
        normalized = value.replace(",", "").replace(".", "")
        bare = normalized.removesuffix("%")
        if bare.isdigit() and 1900 <= int(bare) <= 2100:
            continue
        values.add(normalized)
    return sorted(values)


async def _execute(pipeline: RealPipeline, query: str, language: str, scope: Sequence[str]) -> dict[str, Any]:
    traces: list[dict[str, Any]] = []
    pipeline.telemetry_sink = traces.append

    async def connected() -> bool:
        return False

    answer = ""
    async for event in pipeline.stream(
        query, connected, conversation_context=LanguageContext(language), company_scope=list(scope)
    ):
        if event.event == "delta":
            answer += str(event.data.get("text", ""))
    trace = traces[-1]
    return {
        "answer": answer,
        "route": trace["route"]["route"],
        "resolved_tickers": trace.get("resolver", {}).get("resolved_tickers", list(scope)),
        "final_evidence_ids": trace["final_generation_evidence_ids"],
        "citation_ids": trace["resolved_used_ids"],
        "numbers": _substantive_numbers(answer),
        "safe_error_class": trace["safe_error_class"],
    }


async def evaluate_pairs(pairs: Sequence[dict[str, Any]], pipeline: RealPipeline) -> dict[str, Any]:
    gold = load_gold()
    records: list[dict[str, Any]] = []
    for pair in pairs:
        gold_case = gold[GOLD_CASE_IDS[pair["case_id"]]]
        try:
            english = await _execute(pipeline, pair["en"], "en", pair["expected_tickers"])
            serbian = await _execute(pipeline, pair["sr"], "sr", pair["expected_tickers"])
        except Exception as error:
            records.append({
                "case_id": pair["case_id"], "focus": pair["focus"],
                "gold_case_id": gold_case["case_id"], "gold_chunk_ids": gold_case["gold_chunk_ids"],
                "error": {"type": type(error).__name__},
                "wording_review": "not_run_due_to_execution_error",
            })
            continue
        gold_ids = set(gold_case["gold_chunk_ids"])
        expected_numbers = _substantive_numbers(
            " ".join(claim["text"] for claim in gold_case["gold_claims"])
        )
        records.append({
            "case_id": pair["case_id"], "focus": pair["focus"],
            "gold_case_id": gold_case["case_id"], "gold_chunk_ids": sorted(gold_ids),
            "expected_numbers": expected_numbers,
            "english": english, "serbian": serbian,
            "company_resolution_match": english["resolved_tickers"] == serbian["resolved_tickers"] == pair["expected_tickers"],
            "route_match": english["route"] == serbian["route"] == gold_case["expected_route"],
            "gold_chunk_recall_match": bool(gold_ids & set(english["final_evidence_ids"])) == bool(gold_ids & set(serbian["final_evidence_ids"])),
            "numerical_values_match": (
                not expected_numbers
                or (
                    set(expected_numbers).issubset(english["numbers"])
                    and set(expected_numbers).issubset(serbian["numbers"])
                )
            ),
            "citation_ids_match": set(english["citation_ids"]) == set(serbian["citation_ids"]),
            "wording_review": "pending_human_or_diagnostic_review",
        })
    gates = ("company_resolution_match", "route_match", "gold_chunk_recall_match", "numerical_values_match", "citation_ids_match")
    scored = [record for record in records if "error" not in record]
    return {
        "schema_version": 1, "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "pair_count": len(records),
        "summary": {
            "error_count": len(records) - len(scored),
            **{key: sum(bool(record[key]) for record in scored) / len(scored) if scored else 0.0 for key in gates},
        },
        "records": records,
    }


def write_result(result: dict[str, Any], output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "raw.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in result["records"]), encoding="utf-8"
    )
    (output_directory / "summary.json").write_text(
        json.dumps({key: value for key, value in result.items() if key != "records"}, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--freeze-manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    validate_manifest(args.freeze_manifest)
    settings = ApplicationSettings.from_environment()
    pipeline = RealPipeline.build(settings.pipeline, settings.provider)
    try:
        result = asyncio.run(evaluate_pairs(load_pairs(), pipeline))
    finally:
        pipeline.close()
    write_result(result, args.output_directory)
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
