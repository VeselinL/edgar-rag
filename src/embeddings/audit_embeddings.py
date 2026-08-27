"""Validate the complete BGE-base embedding release and write its manifest."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src.embeddings.embed_chunks import (
    DEFAULT_MODEL_NAME,
    MODEL_CONFIGS,
    validate_embedding_artifacts,
)
from src.filings.corpus import ACTIVE_FILINGS
from src.filings.audit_tables import sha256_file


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def audit_embedding_release(
    chunks_directory: Path,
    embeddings_directory: Path,
    input_manifest_path: Path | None = None,
    *,
    model_name: str = DEFAULT_MODEL_NAME,
    release_id: str = "bgebase-table-v2-chunk-v3.20260813",
) -> dict:
    if input_manifest_path is None:
        input_manifest = None
        inputs = [
            {
                "ticker": ticker,
                "filing_year": int(filing_name.split("-", maxsplit=1)[0]),
            }
            for ticker, filing_name in ACTIVE_FILINGS.items()
        ]
        declared_company_count = len(ACTIVE_FILINGS)
    else:
        input_manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
        inputs = input_manifest.get("files") or []
        declared_company_count = input_manifest.get("company_count")
    failures = []
    records = []
    if (
        not isinstance(declared_company_count, int)
        or declared_company_count < 1
        or len(inputs) != declared_company_count
    ):
        failures.append("input manifest company_count does not match its filings")
    config = MODEL_CONFIGS[model_name]
    for source in inputs:
        ticker = source["ticker"]
        year = source["filing_year"]
        chunks_path = chunks_directory / ticker / f"{year}-10-K.chunks.jsonl"
        embedding_path = (
            embeddings_directory
            / ticker
            / f"{year}-10-K.{model_name}.embeddings.npz"
        )
        manifest_path = embedding_path.with_suffix(".manifest.json")
        record = {
            "ticker": ticker,
            "filing_year": year,
            "chunks_path": str(chunks_path.resolve()),
            "embedding_path": str(embedding_path.resolve()),
            "manifest_path": str(manifest_path.resolve()),
        }
        try:
            validation = validate_embedding_artifacts(
                chunks_path, embedding_path, manifest_path
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            record.update(
                {
                    **validation,
                    "manifest_sha256": sha256_file(manifest_path),
                    "manifest_schema_version": manifest["schema_version"],
                    "chunk_schema_version": manifest["chunk_schema_version"],
                    "table_schema_version": manifest["table_schema_version"],
                    "table_heuristics_version": manifest[
                        "table_heuristics_version"
                    ],
                    "chunking_config_sha256": manifest[
                        "chunking_config_sha256"
                    ],
                    "ordered_text_hashes_sha256": manifest[
                        "ordered_text_hashes_sha256"
                    ],
                    "truncated_input_count": manifest["truncated_input_count"],
                    "truncated_table_count": manifest["truncated_table_count"],
                    "truncated_narrative_count": manifest[
                        "truncated_narrative_count"
                    ],
                    "truncated_content_type_counts": manifest[
                        "truncated_content_type_counts"
                    ],
                    "truncated_table_kind_counts": manifest[
                        "truncated_table_kind_counts"
                    ],
                }
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
            record["failure"] = str(error)
            failures.append(f"{ticker}: {error}")
        records.append(record)

    dimensions = {record.get("dimension") for record in records if "dimension" in record}
    dtypes = {record.get("dtype") for record in records if "dtype" in record}
    source_hashes = [
        record.get("source_chunks_sha256") for record in records if not record.get("failure")
    ]
    if (
        not isinstance(declared_company_count, int)
        or len(records) != declared_company_count
        or len(source_hashes) != declared_company_count
    ):
        failures.append("embedding release is incomplete")
    if dimensions != {config["expected_dimension"]}:
        failures.append(f"embedding dimensions are not uniformly {config['expected_dimension']}")
    if dtypes != {"float32"}:
        failures.append("embedding dtype is not uniformly float32")
    if len(source_hashes) != len(set(source_hashes)):
        failures.append("embedding records do not bind distinct chunk files")

    return {
        "schema_version": 1,
        "release_id": release_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_name": model_name,
        "model_repository": config["repository"],
        "requested_model_revision": config["revision"],
        "query_prefix": config["query_prefix"],
        "document_prefix": config["document_prefix"],
        "expected_dimension": config["expected_dimension"],
        "expected_max_sequence_length": config["expected_max_sequence_length"],
        "input_manifest": (
            str(input_manifest_path.resolve()) if input_manifest_path is not None else None
        ),
        "input_manifest_sha256": (
            sha256_file(input_manifest_path) if input_manifest_path is not None else None
        ),
        "company_count": len(records),
        "vector_count": sum(record.get("chunk_count", 0) for record in records),
        "truncated_input_count": sum(
            record.get("truncated_input_count", 0) for record in records
        ),
        "truncated_table_count": sum(
            record.get("truncated_table_count", 0) for record in records
        ),
        "truncated_narrative_count": sum(
            record.get("truncated_narrative_count", 0) for record in records
        ),
        "truncated_table_kind_counts": dict(
            sorted(
                Counter(
                    {
                        kind: sum(
                            record.get("truncated_table_kind_counts", {}).get(kind, 0)
                            for record in records
                        )
                        for kind in {
                            kind
                            for record in records
                            for kind in record.get(
                                "truncated_table_kind_counts", {}
                            )
                        }
                    }
                ).items()
            )
        ),
        "records": records,
        "failures": failures,
        "failure_count": len(failures),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate every manifest-v3 embedding artifact in the input manifest."
    )
    parser.add_argument(
        "--chunks-directory", type=Path, default=PROJECT_ROOT / "data" / "chunks"
    )
    parser.add_argument(
        "--embeddings-directory",
        type=Path,
        default=PROJECT_ROOT / "data" / "embeddings",
    )
    parser.add_argument(
        "--input-manifest",
        type=Path,
        help="Optional historical/release manifest; defaults to the active corpus registry.",
    )
    parser.add_argument("--model-name", choices=tuple(MODEL_CONFIGS), default="bgebase")
    parser.add_argument(
        "--release-id", default="bgebase-table-v2-chunk-v3.20260813"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    arguments = parser.parse_args()
    report = audit_embedding_release(
        arguments.chunks_directory,
        arguments.embeddings_directory,
        arguments.input_manifest,
        model_name=arguments.model_name,
        release_id=arguments.release_id,
    )
    if arguments.output:
        write_json_atomic(arguments.output, report)
        print(f"Wrote embedding release audit to {arguments.output}")
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    if arguments.strict and report["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
