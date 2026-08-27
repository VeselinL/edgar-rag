"""Opt-in preprocessing adapter for Rivian's title-only layout tables.

This module deliberately leaves the shared filing extractor unchanged.  Rivian's
filing contains presentational tables made only of a title and/or units; the
shared table normalizer correctly excludes those rows but expects a remaining
body row.  During this adapter's process only, those fragments are retained as
ordinary text blocks while all substantive tables use the normal extractor.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.filings import block_extraction
from src.filings.dom_processing import (
    collect_visible_text,
    drop_hidden_nodes,
    drop_non_text_nodes,
    drop_page_furniture,
    drop_xbrl_tags,
    find_source_anchor,
)
from src.filings.fetch_data import COMPANIES
from src.filings.filing_io import (
    find_latest_local_filing,
    load_extraction_metadata,
    parse_filing_html,
)
from src.filings.preprocess_filing import (
    HTML_TABLE_FINGERPRINT_VERSION,
    LXML_VERSION,
    TABLE_CONTENT_TYPES,
    TABLE_HEURISTICS_VERSION,
    TABLE_SCHEMA_VERSION,
    sha256_file,
    table_quality_metrics,
    write_blocks_jsonl,
    write_json_atomic,
)
from src.filings.table_processing import (
    _form_row_atoms,
    _origin_rows,
    build_row_profiles,
    detect_table_header_rows,
    extract_table_structure,
    identify_promoted_rows,
    is_semantic_bullet_table,
)


def is_title_only_layout_table(node, html_table_id: str) -> bool:
    """Return whether a non-empty table has no data row after normal exclusions."""
    structure = extract_table_structure(node, html_table_id)
    if not structure["physical_rows"] or is_semantic_bullet_table(structure):
        return False
    promoted = identify_promoted_rows(structure)
    promoted_rows = set(promoted["internal_title_rows"] + promoted["unit_rows"])
    profiles = build_row_profiles(structure, excluded_rows=promoted_rows)
    header = detect_table_header_rows(
        structure,
        profiles,
        excluded_rows=promoted_rows,
    )
    excluded_rows = promoted_rows | set(header["header_row_source_indexes"])
    return not any(
        row_index not in excluded_rows and _form_row_atoms(cells)
        for row_index, cells in _origin_rows(structure).items()
    )


def layout_table_text(node, html_table_id: str) -> str:
    """Render the visible rows with separators that presentational HTML omits."""
    structure = extract_table_structure(node, html_table_id)
    rows = [" ".join(value for value in row if value) for row in structure["physical_rows"]]
    return "\n".join(row for row in rows if row)


def extract_rivian_blocks(document, filing_year: int, metadata: dict) -> list[dict]:
    """Run the stable extractor with a process-local title-table adaptation."""
    original_emit_table = block_extraction.emit_table

    def emit_table_or_layout_text(node, context, *, html_table_index=None):
        table_index = html_table_index or context.html_table_index
        html_table_id = f"{context.ticker}-{context.filing_year}-HTMLTABLE-{table_index:04d}"
        if not is_title_only_layout_table(node, html_table_id):
            return original_emit_table(
                node,
                context,
                html_table_index=html_table_index,
            )

        text = layout_table_text(node, html_table_id) or collect_visible_text(node)
        if not text:
            return None
        return block_extraction.append_block(
            context,
            content_type="paragraph",
            text=text,
            source_tag="table_layout",
            source_anchor=find_source_anchor(node),
            extra={
                "html_table_id": html_table_id,
                "html_table_index": table_index,
                "layout_table_adapter": "rivian-title-only-v1",
            },
        )

    block_extraction.emit_table = emit_table_or_layout_text
    try:
        return block_extraction.extract_blocks(
            document,
            "RIVN",
            filing_year,
            metadata=metadata,
        )
    finally:
        block_extraction.emit_table = original_emit_table


def process_rivian(*, raw_directory: str | Path, processed_directory: str | Path, overwrite: bool) -> Path:
    filing_year, filing_path = find_latest_local_filing("rivian", raw_directory)
    document = parse_filing_html(filing_path.read_bytes())
    metadata = load_extraction_metadata(
        filing_path,
        document,
        COMPANIES["rivian"],
        filing_year,
    )
    for cleaner in (drop_non_text_nodes, drop_hidden_nodes, drop_xbrl_tags, drop_page_furniture):
        cleaner(document)

    blocks = extract_rivian_blocks(document, filing_year, metadata)
    output_path = Path(processed_directory) / "RIVN" / f"{filing_year}-10-K.blocks.jsonl"
    written_path = write_blocks_jsonl(blocks, output_path, overwrite=overwrite)
    table_blocks = [block for block in blocks if block["content_type"] in TABLE_CONTENT_TYPES]
    quality = [table_quality_metrics(block) for block in table_blocks]
    write_json_atomic(
        {
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
            "raw_cell_accounting_min": min((item["raw_cell_accounting_coverage"] for item in quality), default=1.0),
            "standalone_marker_count": sum(item["standalone_marker_count"] for item in quality),
            "markdown_valid_count": sum(item["markdown_valid"] for item in quality),
            "normalization_fallback_count": sum(item["fallback_used"] for item in quality),
            "adapter": "rivian-title-only-layout-v1",
        },
        output_path.with_suffix(".qa.json"),
        overwrite=overwrite,
    )
    return written_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Process Rivian with an isolated title-table adapter.")
    parser.add_argument("--raw-directory", default="data/raw")
    parser.add_argument("--processed-directory", default="data/processed")
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()
    output_path = process_rivian(
        raw_directory=arguments.raw_directory,
        processed_directory=arguments.processed_directory,
        overwrite=arguments.overwrite,
    )
    print(f"Wrote Rivian processed filing blocks to {output_path}")


if __name__ == "__main__":
    main()
