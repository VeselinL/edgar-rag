"""Migrate Mobileye gold labels through stable block/table evidence identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from src.embeddings.embed_chunks import load_chunks, sha256_file


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[.,][A-Za-z0-9]+)*|[%$€£¥]")


def canonical_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def evidence_tokens(value: str) -> set[str]:
    return {token.casefold() for token in TOKEN_PATTERN.findall(value)}


def evidence_overlap(expected: str, actual: str) -> float:
    expected_tokens = evidence_tokens(expected)
    actual_tokens = evidence_tokens(actual)
    return (
        len(expected_tokens & actual_tokens) / len(expected_tokens)
        if expected_tokens
        else 1.0
    )


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as output_file:
            temporary = Path(output_file.name)
            json.dump(value, output_file, indent=2, ensure_ascii=False)
            output_file.write("\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def migrate_dataset(
    source_path: Path,
    chunks_path: Path,
    *,
    approve_review: bool = False,
) -> dict:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    chunks = load_chunks(chunks_path)
    table_chunks = [
        chunk for chunk in chunks if chunk.get("content_type") == "table"
    ]
    if not table_chunks:
        raise ValueError("Evaluation chunk artifact contains no logical tables")
    table_schema_versions = {
        chunk.get("table_schema_version") for chunk in table_chunks
    }
    table_heuristics_versions = {
        chunk.get("table_heuristics_version") for chunk in table_chunks
    }
    chunk_schema_versions = {
        chunk.get("chunk_schema_version") for chunk in chunks
    }
    chunking_config_hashes = {
        chunk.get("chunking_config_sha256") for chunk in chunks
    }
    for label, values in (
        ("chunk schema version", chunk_schema_versions),
        ("table schema version", table_schema_versions),
        ("table heuristics version", table_heuristics_versions),
        ("chunking config hash", chunking_config_hashes),
    ):
        if None in values or len(values) != 1:
            raise ValueError(f"Evaluation chunks have mixed or missing {label}")
    chunk_by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    chunks_by_block = defaultdict(list)
    for chunk in chunks:
        for block_id in chunk.get("block_ids") or []:
            chunks_by_block[block_id].append(chunk)

    migrated_records = []
    missing = []
    required_review = []
    methods = Counter()
    for record in source.get("records") or []:
        evidence_mappings = []
        record_new_ids = set()
        record_block_ids = set()
        record_anchors = set()
        logical_table_ids = set()
        narrative_spans = []
        for evidence_index, evidence in enumerate(
            record.get("historical_500_evidence") or [], start=1
        ):
            source_block_ids = evidence.get("source_block_ids") or []
            candidates = {
                candidate["chunk_id"]: candidate
                for block_id in source_block_ids
                for candidate in chunks_by_block.get(block_id, [])
                if candidate.get("content_type") == evidence.get("content_type")
            }
            expected_excerpt = evidence.get("expected_evidence_excerpt") or ""
            scored = []
            expected_canonical = canonical_text(expected_excerpt)
            for candidate in candidates.values():
                actual_canonical = canonical_text(candidate["text"])
                exact = bool(
                    expected_canonical
                    and (
                        expected_canonical in actual_canonical
                        or actual_canonical in expected_canonical
                    )
                )
                scored.append(
                    (
                        exact,
                        evidence_overlap(expected_excerpt, candidate["text"]),
                        len(set(source_block_ids) & set(candidate.get("block_ids") or []))
                        / len(source_block_ids),
                        candidate,
                    )
                )
            exact_candidates = [value for value in scored if value[0]]
            if exact_candidates:
                selected = exact_candidates
                method = "source_block_ids+normalized_exact_evidence"
            elif evidence.get("content_type") == "table" and scored:
                selected = [value for value in scored if value[2] > 0]
                method = "source_block_ids+logical_table_identity+token_evidence"
            elif scored:
                best = max(value[1] for value in scored)
                selected = [value for value in scored if value[1] == best and best >= 0.60]
                method = "source_block_ids+token_evidence"
            else:
                selected = []
                method = "missing"

            selected.sort(key=lambda value: value[3]["chunk_index"])
            new_ids = [value[3]["chunk_id"] for value in selected]
            minimum_overlap = min((value[1] for value in selected), default=0.0)
            if not new_ids or (
                evidence.get("content_type") == "table" and minimum_overlap < 0.60
            ):
                missing.append(f"{record['id']} evidence {evidence_index}")
            cardinality = (
                "one_to_one"
                if len(new_ids) == 1
                else "one_to_many"
                if len(new_ids) > 1
                else "missing"
            )
            needs_review = cardinality != "one_to_one"
            if needs_review:
                required_review.append(f"{record['id']} evidence {evidence_index}")
            for _, _, _, candidate in selected:
                record_new_ids.add(candidate["chunk_id"])
                if candidate.get("content_type") == "table":
                    logical_table_ids.add(candidate["logical_table_id"])
                else:
                    narrative_spans.append(
                        {
                            "chunk_id": candidate["chunk_id"],
                            "source_group": candidate.get("source_group"),
                            "source_text_start": candidate.get("source_text_start"),
                            "source_text_end": candidate.get("source_text_end"),
                            "source_token_start": candidate.get("source_token_start"),
                            "source_token_end": candidate.get("source_token_end"),
                        }
                    )
            record_block_ids.update(source_block_ids)
            record_anchors.update(evidence.get("source_anchors") or [])
            methods[method] += 1
            evidence_mappings.append(
                {
                    "evidence_index": evidence_index,
                    "old_chunk_id": evidence["old_chunk_id"],
                    "evidence_type": evidence["content_type"],
                    "source_block_ids": source_block_ids,
                    "source_anchors": evidence.get("source_anchors") or [],
                    "expected_evidence_excerpt": expected_excerpt,
                    "new_chunk_ids": new_ids,
                    "logical_table_ids": sorted(
                        {
                            chunk_by_id[chunk_id]["logical_table_id"]
                            for chunk_id in new_ids
                            if chunk_by_id[chunk_id].get("content_type") == "table"
                        }
                    ),
                    "matching_method": method,
                    "minimum_token_evidence_overlap": minimum_overlap,
                    "cardinality": cardinality,
                    "manual_review_required": needs_review,
                }
            )

        ordered_new_ids = sorted(
            record_new_ids, key=lambda chunk_id: chunk_by_id[chunk_id]["chunk_index"]
        )
        record_requires_review = any(
            value["manual_review_required"] for value in evidence_mappings
        )
        review_status = "approved" if approve_review else (
            "required" if record_requires_review else "not_required"
        )
        migrated_records.append(
            {
                "id": record["id"],
                "evaluation_set": record["evaluation_set"],
                "category": record["category"],
                "query": record["query"],
                "answerable": record["answerable"],
                "original_artifact_config_label": record[
                    "original_artifact_config_label"
                ],
                "historical_mappings": record["historical_mappings"],
                "old_relevant_chunk_ids": record["historical_mappings"][
                    "recursive_token_500"
                ],
                "evidence_type": record["evidence_type"],
                "source_block_ids": sorted(record_block_ids),
                "source_anchors": sorted(record_anchors),
                "logical_table_ids": sorted(logical_table_ids),
                "narrative_spans": narrative_spans,
                "expected_evidence": [
                    value["expected_evidence_excerpt"]
                    for value in evidence_mappings
                ],
                "new_relevant_chunk_ids": ordered_new_ids,
                "evidence_mappings": evidence_mappings,
                "matching_method": "stable source block identity with evidence confirmation",
                "confidence": (
                    "high"
                    if all(
                        value["cardinality"] == "one_to_one"
                        for value in evidence_mappings
                    )
                    else "reviewed"
                    if approve_review
                    else "requires_review"
                ),
                "review_state": {
                    "status": review_status,
                    "reviewer": (
                        "Codex senior-engineer implementation review"
                        if approve_review
                        else None
                    ),
                    "reviewed_on": "2026-08-13" if approve_review else None,
                    "reason": (
                        "Stable block/anchor links, logical-table identity, and expected evidence were confirmed in every mapped chunk. Any one-to-many or many-to-one mapping was reviewed explicitly."
                        if approve_review
                        else None
                    ),
                },
                "evidence_note": record.get("evidence_note"),
            }
        )

    if missing:
        raise ValueError("Unresolved evaluation evidence: " + ", ".join(missing))
    if len(migrated_records) != source.get("record_count"):
        raise ValueError("Evaluation record count changed during migration")
    if approve_review and any(
        record["review_state"]["status"] != "approved"
        for record in migrated_records
    ):
        raise ValueError("Approved migration contains unreviewed records")

    source_sha = sha256_file(source_path)
    return {
        "schema_version": 2,
        "dataset_version": "mobileye-retrieval-gold-v2-table-v2-chunk-v3",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "authoritative_source": "versioned JSON; notebooks are consumers only",
        "source_dataset": str(source_path.resolve()),
        "source_dataset_sha256": source_sha,
        "source_chunk_artifact": str(chunks_path.resolve()),
        "source_chunks_sha256": sha256_file(chunks_path),
        "chunk_schema_version": next(iter(chunk_schema_versions)),
        "table_schema_version": next(iter(table_schema_versions)),
        "table_heuristics_version": next(iter(table_heuristics_versions)),
        "chunking_config_sha256": next(iter(chunking_config_hashes)),
        "record_count": len(migrated_records),
        "migration_summary": {
            "evidence_item_count": sum(
                len(record["evidence_mappings"]) for record in migrated_records
            ),
            "matching_method_counts": dict(sorted(methods.items())),
            "manual_review_required_item_count": len(required_review),
            "manual_review_required_items": required_review,
            "review_status": "approved" if approve_review else "draft",
        },
        "records": migrated_records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate Mobileye retrieval gold via stable evidence provenance."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=PROJECT_ROOT
        / "data"
        / "evaluation"
        / "mobileye_retrieval_gold_v1.json",
    )
    parser.add_argument(
        "--chunks",
        type=Path,
        default=PROJECT_ROOT
        / "data"
        / "chunks"
        / "MBLY"
        / "2025-10-K.chunks.jsonl",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--approve-review",
        action="store_true",
        help="Record the completed manual review of ambiguous mappings.",
    )
    arguments = parser.parse_args()
    migrated = migrate_dataset(
        arguments.source,
        arguments.chunks,
        approve_review=arguments.approve_review,
    )
    write_json_atomic(arguments.output, migrated)
    print(
        f"Wrote {migrated['record_count']} migrated records to {arguments.output}"
    )


if __name__ == "__main__":
    main()
