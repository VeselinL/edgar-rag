"""Promote one audited table-v2/chunk-v3 corpus as an aligned release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.chunking.chunk_documents import CHUNK_SCHEMA_VERSION
from src.filings.release_state import TABLE_MIGRATION_MARKER
from src.filings.table_processing import (
    TABLE_HEURISTICS_VERSION,
    TABLE_SCHEMA_VERSION,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as input_file:
        for value in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(value)
    return digest.hexdigest()


def load_json(path: str | Path) -> dict:
    with Path(path).open(encoding="utf-8") as input_file:
        value = json.load(input_file)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def write_json_atomic(path: Path, value: dict, *, overwrite: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Release metadata already exists: {path}")
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


def copy_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with source.open("rb") as input_file, tempfile.NamedTemporaryFile(
            "wb", dir=target.parent, delete=False
        ) as output_file:
            temporary = Path(output_file.name)
            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary, target)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def release_inventory(stage_root: Path, input_manifest: dict) -> list[dict]:
    records = []
    files = input_manifest.get("files") or []
    company_count = input_manifest.get("company_count")
    if not isinstance(company_count, int) or company_count < 1 or len(files) != company_count:
        raise ValueError("Approved input manifest company_count must match its filings")
    if len({record.get("ticker") for record in files}) != company_count:
        raise ValueError("Approved input manifest contains duplicate tickers")
    for record in files:
        ticker = record["ticker"]
        year = record["filing_year"]
        mappings = (
            (
                "processed_blocks",
                stage_root / "processed" / ticker / f"{year}-10-K.blocks.jsonl",
                DATA_ROOT / "processed" / ticker / f"{year}-10-K.blocks.jsonl",
            ),
            (
                "processed_qa",
                stage_root / "processed" / ticker / f"{year}-10-K.blocks.qa.json",
                DATA_ROOT / "processed" / ticker / f"{year}-10-K.blocks.qa.json",
            ),
            (
                "chunks",
                stage_root / "chunks" / ticker / f"{year}-10-K.chunks.jsonl",
                DATA_ROOT / "chunks" / ticker / f"{year}-10-K.chunks.jsonl",
            ),
            (
                "chunk_statistics",
                stage_root / "chunks" / ticker / f"{year}-10-K.chunks.stats.json",
                DATA_ROOT / "chunks" / ticker / f"{year}-10-K.chunks.stats.json",
            ),
        )
        for artifact_type, source, target in mappings:
            if not source.is_file():
                raise FileNotFoundError(f"Missing staged {artifact_type}: {source}")
            records.append(
                {
                    "ticker": ticker,
                    "filing_year": year,
                    "artifact_type": artifact_type,
                    "staged_path": str(source.resolve()),
                    "target_path": str(target.resolve()),
                    "target_relative_path": str(target.relative_to(PROJECT_ROOT)),
                    "sha256": sha256_file(source),
                    "size_bytes": source.stat().st_size,
                }
            )
    return records


def preflight(
    stage_root: Path,
    audit_path: Path,
    review_path: Path,
    input_manifest_path: Path,
) -> tuple[list[dict], dict, dict]:
    audit = load_json(audit_path)
    review = load_json(review_path)
    inputs = load_json(input_manifest_path)
    if audit.get("failures") or audit.get("aggregate", {}).get("failure_count"):
        raise ValueError("Staged audit contains failures")
    if not audit.get("manual_review", {}).get("complete"):
        raise ValueError("Staged audit does not have complete manual review coverage")
    if audit.get("table_schema_version") != TABLE_SCHEMA_VERSION:
        raise ValueError("Staged audit table schema is stale")
    if audit.get("chunk_schema_version") != CHUNK_SCHEMA_VERSION:
        raise ValueError("Staged audit chunk schema is stale")
    if audit.get("table_heuristics_version") != TABLE_HEURISTICS_VERSION:
        raise ValueError("Staged audit heuristic version is stale")
    if audit["manual_review"].get("manifest_sha256") != sha256_file(review_path):
        raise ValueError("Manual review file changed after the approved audit")

    records = release_inventory(stage_root, inputs)
    audit_processed = {
        value["ticker"]: value for value in audit.get("processed") or []
    }
    audit_chunks = {value["ticker"]: value for value in audit.get("chunks") or []}
    if set(audit_processed) != {value["ticker"] for value in inputs["files"]}:
        raise ValueError("Audit processed ticker set differs from the input manifest")
    if set(audit_chunks) != set(audit_processed):
        raise ValueError("Audit chunk ticker set differs from processed artifacts")

    for record in records:
        ticker = record["ticker"]
        artifact_type = record["artifact_type"]
        if artifact_type == "processed_blocks":
            expected = audit_processed[ticker]["processed_sha256"]
            if expected != record["sha256"]:
                raise ValueError(f"{ticker}: processed file changed after audit")
        elif artifact_type == "chunks":
            expected = audit_chunks[ticker]["chunk_sha256"]
            if expected != record["sha256"]:
                raise ValueError(f"{ticker}: chunk file changed after audit")
        elif artifact_type == "processed_qa":
            qa = load_json(record["staged_path"])
            if (
                qa.get("table_schema_version") != TABLE_SCHEMA_VERSION
                or qa.get("table_heuristics_version") != TABLE_HEURISTICS_VERSION
                or qa.get("processed_blocks_sha256")
                != audit_processed[ticker]["processed_sha256"]
            ):
                raise ValueError(f"{ticker}: stale processed QA manifest")
        elif artifact_type == "chunk_statistics":
            stats = load_json(record["staged_path"])
            if (
                stats.get("chunk_schema_version") != CHUNK_SCHEMA_VERSION
                or stats.get("table_schema_version") != TABLE_SCHEMA_VERSION
                or stats.get("table_heuristics_version")
                != TABLE_HEURISTICS_VERSION
                or stats.get("source_block_coverage") != 1.0
                or stats.get("source_anchor_coverage") != 1.0
            ):
                raise ValueError(f"{ticker}: stale or incomplete chunk statistics")

    for record in inputs["files"]:
        raw_path = PROJECT_ROOT / record["filing"]
        if not raw_path.is_file() or "sha256:" + sha256_file(raw_path) != record["raw_sha256"]:
            raise ValueError(f"{record['ticker']}: frozen raw input hash changed")
    return records, audit, review


def promote_release(
    stage_root: Path,
    audit_path: Path,
    review_path: Path,
    input_manifest_path: Path,
    release_manifest_path: Path,
    old_manifest_path: Path,
    backup_root: Path,
    *,
    release_id: str = "table-v2-chunk-v3.20260813",
    superseded_release_manifest_path: Path | None = None,
) -> dict:
    marker = DATA_ROOT / TABLE_MIGRATION_MARKER
    for path in (old_manifest_path, marker):
        if path.exists():
            raise FileExistsError(f"Promotion target already exists: {path}")
    if backup_root.exists():
        raise FileExistsError(f"Backup target already exists: {backup_root}")
    replacing_release_manifest = release_manifest_path.exists()
    if replacing_release_manifest and superseded_release_manifest_path is None:
        raise FileExistsError(
            f"Promotion target already exists: {release_manifest_path}; provide an "
            "explicit superseded-release manifest path"
        )
    if not replacing_release_manifest and superseded_release_manifest_path is not None:
        raise FileNotFoundError(
            f"No release manifest exists to supersede: {release_manifest_path}"
        )
    if (
        superseded_release_manifest_path is not None
        and superseded_release_manifest_path.exists()
    ):
        raise FileExistsError(
            "Superseded release manifest target already exists: "
            f"{superseded_release_manifest_path}"
        )

    records, audit, _ = preflight(
        stage_root, audit_path, review_path, input_manifest_path
    )
    superseded_release_sha256 = None
    if superseded_release_manifest_path is not None:
        superseded_release_sha256 = sha256_file(release_manifest_path)
        copy_atomic(release_manifest_path, superseded_release_manifest_path)
    old_records = []
    backup_root.mkdir(parents=True)
    for record in records:
        target = Path(record["target_path"])
        old_record = {
            "ticker": record["ticker"],
            "filing_year": record["filing_year"],
            "artifact_type": record["artifact_type"],
            "target_relative_path": record["target_relative_path"],
            "existed": target.is_file(),
        }
        if target.is_file():
            relative = target.relative_to(DATA_ROOT)
            backup_path = backup_root / relative
            copy_atomic(target, backup_path)
            old_record.update(
                {
                    "sha256": sha256_file(target),
                    "size_bytes": target.stat().st_size,
                    "backup_path": str(backup_path.resolve()),
                    "backup_sha256": sha256_file(backup_path),
                }
            )
        old_records.append(old_record)
    old_manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "rollback inventory captured immediately before table-v2 promotion",
        "backup_root": str(backup_root.resolve()),
        "files": old_records,
    }
    write_json_atomic(old_manifest_path, old_manifest)

    marker_payload = {
        "release": release_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "expected_file_count": len(records),
        "approved_audit_sha256": sha256_file(audit_path),
    }
    with marker.open("x", encoding="utf-8") as marker_file:
        json.dump(marker_payload, marker_file, indent=2)
        marker_file.write("\n")
        marker_file.flush()
        os.fsync(marker_file.fileno())

    # If any operation below fails, intentionally leave the marker in place.
    for record in records:
        copy_atomic(Path(record["staged_path"]), Path(record["target_path"]))
    for record in records:
        target = Path(record["target_path"])
        if sha256_file(target) != record["sha256"]:
            raise RuntimeError(f"Promoted hash mismatch: {target}")

    release_manifest = {
        "schema_version": 1,
        "release_id": release_id,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "table_schema_version": TABLE_SCHEMA_VERSION,
        "chunk_schema_version": CHUNK_SCHEMA_VERSION,
        "table_heuristics_version": TABLE_HEURISTICS_VERSION,
        "company_count": len({record["ticker"] for record in records}),
        "artifact_file_count": len(records),
        "input_manifest": str(input_manifest_path.resolve()),
        "input_manifest_sha256": sha256_file(input_manifest_path),
        "approved_audit": str(audit_path.resolve()),
        "approved_audit_sha256": sha256_file(audit_path),
        "manual_review": str(review_path.resolve()),
        "manual_review_sha256": sha256_file(review_path),
        "old_artifact_manifest": str(old_manifest_path.resolve()),
        "old_artifact_manifest_sha256": sha256_file(old_manifest_path),
        "superseded_release_manifest": (
            str(superseded_release_manifest_path.resolve())
            if superseded_release_manifest_path is not None
            else None
        ),
        "superseded_release_manifest_sha256": superseded_release_sha256,
        "chunking_config": str((DATA_ROOT / "chunks" / "chunking-config.json").resolve()),
        "chunking_config_sha256": sha256_file(
            DATA_ROOT / "chunks" / "chunking-config.json"
        ),
        "audit_aggregate": audit["aggregate"],
        "files": records,
    }
    write_json_atomic(
        release_manifest_path,
        release_manifest,
        overwrite=replacing_release_manifest,
    )
    marker.unlink()
    return release_manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Promote an audited table-v2/chunk-v3 staging tree."
    )
    parser.add_argument("--stage-root", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--manual-review", type=Path, required=True)
    parser.add_argument(
        "--input-manifest",
        type=Path,
        default=DATA_ROOT / "manifests" / "table-v2-inputs.json",
    )
    parser.add_argument(
        "--release-manifest",
        type=Path,
        default=DATA_ROOT / "manifests" / "table-v2-release.json",
    )
    parser.add_argument(
        "--old-manifest",
        type=Path,
        default=DATA_ROOT / "manifests" / "table-v1-artifacts.json",
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=PROJECT_ROOT / "table-v1-backup.20260813",
    )
    parser.add_argument(
        "--release-id",
        default="table-v2-chunk-v3.20260813",
    )
    parser.add_argument(
        "--superseded-release-manifest",
        type=Path,
        help=(
            "Required when replacing an existing release manifest; the prior "
            "manifest is copied atomically to this new path first."
        ),
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate the complete staged release without writing live artifacts.",
    )
    arguments = parser.parse_args()
    if arguments.preflight_only:
        records, _, _ = preflight(
            arguments.stage_root,
            arguments.audit,
            arguments.manual_review,
            arguments.input_manifest,
        )
        print(f"Promotion preflight passed for {len(records)} aligned artifacts")
        return
    release = promote_release(
        arguments.stage_root,
        arguments.audit,
        arguments.manual_review,
        arguments.input_manifest,
        arguments.release_manifest,
        arguments.old_manifest,
        arguments.backup_root,
        release_id=arguments.release_id,
        superseded_release_manifest_path=arguments.superseded_release_manifest,
    )
    print(
        f"Promoted {release['artifact_file_count']} artifacts for "
        f"{release['company_count']} companies"
    )


if __name__ == "__main__":
    main()
