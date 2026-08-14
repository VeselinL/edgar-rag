import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .block_extraction import (
    extract_blocks,
)
from .dom_processing import (
    drop_hidden_nodes,
    drop_non_text_nodes,
    drop_page_furniture,
    drop_xbrl_tags,
)
from .fetch_data import COMPANIES
from .filing_io import (
    find_latest_local_filing,
    load_extraction_metadata,
    parse_filing_html,
)
from .table_processing import (
    HTML_TABLE_FINGERPRINT_VERSION,
    LXML_VERSION,
    TABLE_HEURISTICS_VERSION,
    TABLE_SCHEMA_VERSION,
    table_quality_metrics,
    validate_logical_table,
)


BLOCK_FIELDS = (
    "block_id",
    "block_index",
    "company",
    "ticker",
    "cik",
    "form",
    "filing_year",
    "filing_date",
    "reporting_period",
    "accession_number",
    "section",
    "section_path",
    "document_region",
    "effective_section_path",
    "content_type",
    "text",
    "source_tag",
    "source_anchor",
    "page_start",
    "page_end",
    "source_url",
)

TABLE_CONTENT_TYPES = {"data_table", "text_table", "unknown_table", "navigation"}


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as input_file:
        for value in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(value)
    return digest.hexdigest()


def validate_table_block(
    block: dict,
    seen_block_ids: set[str],
    seen_raw_cell_ids: set[str],
) -> None:
    """Reject stale or lossy logical-table artifacts before serialization."""
    block_id = block["block_id"]
    if block.get("table_schema_version") != TABLE_SCHEMA_VERSION:
        raise ValueError(f"Table {block_id} has a stale table schema")
    if block.get("table_heuristics_version") != TABLE_HEURISTICS_VERSION:
        raise ValueError(f"Table {block_id} has an unknown heuristics version")
    for field in (
        "html_table_id",
        "html_table_index",
        "html_table_xpath",
        "html_table_fingerprint",
        "html_table_fingerprint_version",
        "logical_table_id",
        "table_fragment_index",
    ):
        if not block.get(field):
            raise ValueError(f"Table {block_id} is missing {field}")
    expected_html_id = (
        f"{block['ticker']}-{block['filing_year']}-HTMLTABLE-"
        f"{block['html_table_index']:04d}"
    )
    if block["html_table_id"] != expected_html_id:
        raise ValueError(f"Table {block_id} has inconsistent HTML identity")
    if block["html_table_fingerprint_version"] != HTML_TABLE_FINGERPRINT_VERSION:
        raise ValueError(f"Table {block_id} has an unknown fingerprint version")
    if block.get("is_continuation"):
        previous = block.get("continued_from_block_id")
        if not previous or previous not in seen_block_ids:
            raise ValueError(f"Table {block_id} has an invalid continuation source")
        if block["table_fragment_index"] <= 1:
            raise ValueError(f"Table {block_id} has an invalid fragment index")
    elif block["table_fragment_index"] != 1:
        raise ValueError(f"Table {block_id} starts at a non-first fragment index")

    raw_ids = [cell.get("raw_cell_id") for cell in block.get("raw_cells") or []]
    if not raw_ids or any(not value for value in raw_ids) or len(raw_ids) != len(set(raw_ids)):
        raise ValueError(f"Table {block_id} has invalid raw-cell identities")
    raw_id_set = set(raw_ids)
    referenced_ids = {
        raw_id
        for row in block.get("logical_cell_sources") or []
        for cell in row
        for raw_id in cell
    }
    referenced_ids.update(
        raw_id
        for metadata in block.get("logical_column_header_metadata") or []
        for raw_id in metadata.get("source_raw_cell_ids") or []
    )
    referenced_ids.update(block.get("logical_header_context_source_raw_cell_ids") or [])
    referenced_ids.update(block.get("title_source_raw_cell_ids") or [])
    referenced_ids.update(
        raw_id
        for metadata in block.get("logical_column_unit_metadata") or []
        for raw_id in metadata.get("source_raw_cell_ids") or []
    )
    referenced_ids.update(
        value.get("raw_cell_id")
        for value in (block.get("normalization_diagnostics") or {}).get("ignored_raw_cells", [])
    )
    unknown_references = referenced_ids - raw_id_set - seen_raw_cell_ids
    if unknown_references:
        raise ValueError(
            f"Table {block_id} references unknown raw cells: "
            f"{sorted(unknown_references)[:5]}"
        )
    metrics = table_quality_metrics(block)
    if metrics["raw_cell_accounting_coverage"] != 1.0:
        raise ValueError(
            f"Table {block_id} has incomplete raw-cell accounting: "
            f"{metrics['raw_cell_accounting_coverage']:.6f}"
        )
    validate_logical_table(block, strict=True)


def validate_blocks(blocks: list[dict]) -> None:
    """Validate required fields and deterministic ordering before serialization."""
    if not blocks:
        raise ValueError("Cannot serialize an empty block collection")

    seen_block_ids: set[str] = set()
    seen_raw_cell_ids: set[str] = set()
    seen_html_table_ids: set[str] = set()
    for expected_index, block in enumerate(blocks, start=1):
        missing_fields = [
            field_name for field_name in BLOCK_FIELDS if field_name not in block
        ]
        if missing_fields:
            raise ValueError(
                f"Block {expected_index} is missing required fields: {missing_fields}"
            )
        if block["block_index"] != expected_index:
            raise ValueError(
                f"Expected block_index {expected_index}, got {block['block_index']}"
            )
        if not isinstance(block["text"], str) or not block["text"].strip():
            raise ValueError(f"Block {expected_index} has no usable text")

        expected_block_id = (
            f"{block['ticker']}-{block['filing_year']}-{expected_index:06d}"
        )
        if block["block_id"] != expected_block_id:
            raise ValueError(
                f"Expected block_id {expected_block_id}, got {block['block_id']}"
            )

        if block["content_type"] in TABLE_CONTENT_TYPES:
            validate_table_block(block, seen_block_ids, seen_raw_cell_ids)
            if block["html_table_id"] in seen_html_table_ids:
                raise ValueError(f"Duplicate html_table_id: {block['html_table_id']}")
            seen_html_table_ids.add(block["html_table_id"])
            seen_raw_cell_ids.update(
                cell["raw_cell_id"] for cell in block.get("raw_cells") or []
            )

        try:
            json.dumps(block, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Block {expected_index} is not JSON serializable") from exc
        seen_block_ids.add(block["block_id"])


def write_json_atomic(value: dict, output_path: Path, *, overwrite: bool) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output_path}")
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(value, temporary_file, indent=2)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return output_path


def write_blocks_jsonl(
    blocks: list[dict],
    output_path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Atomically write one validated block per line without touching raw data."""
    validate_blocks(blocks)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Processed filing already exists: {output_path}. "
            "Pass overwrite=True only when you intentionally want to rebuild it."
        )

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            for block in blocks:
                temporary_file.write(json.dumps(block, ensure_ascii=False) + "\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        if overwrite:
            os.replace(temporary_path, output_path)
        else:
            try:
                os.link(temporary_path, output_path)
            except FileExistsError as exc:
                raise FileExistsError(
                    f"Processed filing already exists: {output_path}"
                ) from exc
            temporary_path.unlink()
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    return output_path


def extract_filing_to_jsonl(
    company_name: str,
    raw_directory: str | Path = "data/raw",
    processed_directory: str | Path = "data/processed",
    *,
    overwrite: bool = False,
) -> Path:
    """Clean the latest local 10-K and serialize its ordered blocks to JSONL."""
    company_key = company_name.strip().lower()
    if company_key not in COMPANIES:
        raise ValueError(f"Unknown company: {company_name}")

    filing_year, filing_path = find_latest_local_filing(company_key, raw_directory)
    document = parse_filing_html(filing_path.read_bytes())
    company_info = COMPANIES[company_key]
    metadata = load_extraction_metadata(
        filing_path,
        document,
        company_info,
        filing_year,
    )

    drop_non_text_nodes(document)
    drop_hidden_nodes(document)
    drop_xbrl_tags(document)
    drop_page_furniture(document)

    blocks = extract_blocks(
        document,
        company_info["ticker"],
        filing_year,
        metadata=metadata,
    )
    output_path = (
        Path(processed_directory)
        / company_info["ticker"]
        / f"{filing_year}-10-K.blocks.jsonl"
    )
    qa_path = output_path.with_suffix(".qa.json")
    if not overwrite:
        existing = [path for path in (output_path, qa_path) if path.exists()]
        if existing:
            raise FileExistsError(
                "Processed release output already exists: "
                + ", ".join(str(path) for path in existing)
            )
    written_path = write_blocks_jsonl(blocks, output_path, overwrite=overwrite)
    table_blocks = [block for block in blocks if block["content_type"] in TABLE_CONTENT_TYPES]
    quality = [table_quality_metrics(block) for block in table_blocks]
    qa_manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_raw": str(filing_path.resolve()),
        "source_raw_sha256": sha256_file(filing_path),
        "processed_blocks": str(written_path.resolve()),
        "processed_blocks_sha256": sha256_file(written_path),
        "table_schema_version": TABLE_SCHEMA_VERSION,
        "table_heuristics_version": TABLE_HEURISTICS_VERSION,
        "html_table_fingerprint_version": HTML_TABLE_FINGERPRINT_VERSION,
        "lxml_version": LXML_VERSION,
        "block_count": len(blocks),
        "table_fragment_count": len(table_blocks),
        "raw_cell_accounting_min": min(
            (value["raw_cell_accounting_coverage"] for value in quality),
            default=1.0,
        ),
        "standalone_marker_count": sum(
            value["standalone_marker_count"] for value in quality
        ),
        "markdown_valid_count": sum(value["markdown_valid"] for value in quality),
        "normalization_fallback_count": sum(value["fallback_used"] for value in quality),
    }
    write_json_atomic(
        qa_manifest,
        qa_path,
        overwrite=overwrite,
    )
    return written_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract the latest local normal 10-K into structured JSONL blocks."
    )
    parser.add_argument("company", choices=sorted(COMPANIES))
    parser.add_argument("--raw-directory", default="data/raw")
    parser.add_argument("--processed-directory", default="data/processed")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Intentionally replace an existing processed JSONL file.",
    )
    arguments = parser.parse_args()
    output_path = extract_filing_to_jsonl(
        arguments.company,
        raw_directory=arguments.raw_directory,
        processed_directory=arguments.processed_directory,
        overwrite=arguments.overwrite,
    )
    print(f"Wrote processed filing blocks to {output_path}")


if __name__ == "__main__":
    main()
