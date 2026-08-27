"""Read-only release audit for table-schema-v2 filing artifacts.

The audit intentionally treats processed blocks, chunks, and embeddings as a
single provenance chain.  It never rewrites those artifacts; the only write it
can perform is an atomic machine-readable report requested with ``--output``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from src.chunking.chunk_documents import (
    CHUNK_SCHEMA_VERSION,
    count_tokens,
    get_tokenizer,
    load_chunk_config,
    validate_chunks,
)
from src.embeddings.embed_chunks import (
    DEFAULT_MODEL_NAME,
    embedding_text,
    model_config,
    prepare_document_text,
)
from src.filings.corpus import ACTIVE_COMPANY_COUNT
from src.filings.table_processing import (
    TABLE_HEURISTICS_VERSION,
    TABLE_SCHEMA_VERSION,
    has_explicit_continued_cue,
    table_quality_metrics,
    validate_logical_table,
    validate_markdown,
)
from src.filings.release_state import assert_release_available


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TABLE_CONTENT_TYPES = {"data_table", "text_table", "unknown_table", "navigation"}
INCLUDED_TABLE_CONTENT_TYPES = {"data_table", "text_table", "unknown_table"}
NARRATIVE_CONTENT_TYPES = {"heading", "paragraph", "list_item"}
CURRENCY_OR_PERCENT_MARKERS = {"$", "€", "£", "¥", "%"}
GENERIC_NOTES_TITLE = re.compile(
    r"^notes?\s+to\s+(?:the\s+)?(?:consolidated\s+)?financial\s+statements"
    r"(?:\s*[\-\u2012\u2013\u2014]\s*)?(?:\(continued\)|continued)?$",
    re.IGNORECASE,
)
PAGE_TITLE = re.compile(r"^(?:[A-Z]-)?\d{1,3}$", re.IGNORECASE)
COMPANY_PAGE_TITLE = re.compile(
    r"^[A-Z][A-Z0-9&.,'’ -]+\s(?:AND\s+SUBSIDIARIES|INC\.?|"
    r"CORP(?:ORATION)?\.?|COMPANY|LTD\.?|PLC)$"
)
TITLE_TRUNCATION = re.compile(r"\b(?:and|or|of|to|for|with|by|in|on|at)$", re.I)
TITLE_SOURCE_BUCKETS = {
    "none": "missing",
    "html_caption": "explicit",
    "internal_title_row": "internal",
    "prose_caption": "nearby",
    "heading": "nearby",
    "inherited": "inherited",
}
QUALITY_DENSITY_KINDS = {"financial_data", "structured_text"}
MAX_EMBEDDING_TOKENS = 512


def sha256_file(path: str | Path, *, prefixed: bool = False) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    return f"sha256:{value}" if prefixed else value


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_jsonl(path: str | Path) -> list[dict]:
    assert_release_available(path)
    records = []
    with Path(path).open(encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected object on line {line_number}: {path}")
            records.append(value)
    if not records:
        raise ValueError(f"No records found in {path}")
    return records


def _distribution(values: Iterable[float | int]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {"min": 0, "median": 0, "p95": 0, "max": 0}
    p95_index = max(0, int(len(ordered) * 0.95 + 0.999999) - 1)
    return {
        "min": ordered[0],
        "median": statistics.median(ordered),
        "p95": ordered[p95_index],
        "max": ordered[-1],
    }


def _table_blocks(blocks: list[dict]) -> list[dict]:
    return [
        block for block in blocks if block.get("content_type") in TABLE_CONTENT_TYPES
    ]


def _title_quality_flags(block: dict) -> list[str]:
    title = (block.get("title") or "").strip()
    if not title:
        return ["missing"]
    flags = []
    company = (block.get("company") or "").strip()
    folded = title.casefold()
    if company and folded in {
        company.casefold(),
        f"{company} and subsidiaries".casefold(),
    }:
        flags.append("company_header")
    elif COMPANY_PAGE_TITLE.fullmatch(title):
        flags.append("company_header")
    if GENERIC_NOTES_TITLE.fullmatch(title):
        flags.append("generic_notes")
    if PAGE_TITLE.fullmatch(title):
        flags.append("page_label")
    if len(title) > 500:
        flags.append("overlong")
    if TITLE_TRUNCATION.search(title):
        flags.append("truncated_looking")
    return flags or ["accepted"]


def _validate_physical_evidence(block: dict) -> list[str]:
    failures = []
    block_id = block.get("block_id", "unknown")
    raw_cells = block.get("raw_cells") or []
    raw_ids = [cell.get("raw_cell_id") for cell in raw_cells]
    if not raw_cells or None in raw_ids or len(raw_ids) != len(set(raw_ids)):
        failures.append(f"{block_id}: invalid raw-cell inventory")
    physical_rows = block.get("physical_rows")
    expanded_rows = block.get("physical_expanded_rows")
    source_rows = block.get("physical_source_row_indexes")
    source_columns = block.get("physical_source_column_indexes")
    if not all(isinstance(value, list) for value in (
        physical_rows,
        expanded_rows,
        source_rows,
        source_columns,
    )):
        failures.append(f"{block_id}: missing physical evidence arrays")
        return failures
    width = len(source_columns)
    if (
        len(physical_rows) != len(source_rows)
        or len(expanded_rows) != len(source_rows)
        or any(len(row) != width for row in physical_rows)
        or any(len(row) != width for row in expanded_rows)
    ):
        failures.append(f"{block_id}: inconsistent physical evidence shape")
    if block.get("rows") != physical_rows or block.get("expanded_rows") != expanded_rows:
        failures.append(f"{block_id}: deprecated physical aliases disagree")
    if block.get("source_row_indexes") != source_rows:
        failures.append(f"{block_id}: physical row-index alias disagrees")
    if block.get("source_column_indexes") != source_columns:
        failures.append(f"{block_id}: physical column-index alias disagrees")
    return failures


def _validate_table_identities(tables: list[dict]) -> list[str]:
    failures = []
    html_ids = [block.get("html_table_id") for block in tables]
    if None in html_ids or len(html_ids) != len(set(html_ids)):
        failures.append("missing or duplicate HTML table IDs")
    indexes = [block.get("html_table_index") for block in tables]
    if None in indexes or len(indexes) != len(set(indexes)):
        failures.append("missing or duplicate HTML table indexes")

    groups = defaultdict(list)
    for block in tables:
        ticker = block.get("ticker")
        year = block.get("filing_year")
        index = block.get("html_table_index")
        expected_html_id = (
            f"{ticker}-{year}-HTMLTABLE-{index:04d}"
            if ticker and year and isinstance(index, int)
            else None
        )
        if block.get("html_table_id") != expected_html_id:
            failures.append(f"{block.get('block_id')}: unstable HTML table ID")
        logical_id = block.get("logical_table_id")
        if not logical_id:
            failures.append(f"{block.get('block_id')}: missing logical table ID")
        groups[logical_id].append(block)

    for logical_id, fragments in groups.items():
        fragments.sort(key=lambda value: value["block_index"])
        first = fragments[0]
        expected_logical_id = (
            f"{first['ticker']}-{first['filing_year']}-TABLE-"
            f"{first['html_table_index']:04d}"
        )
        if logical_id != expected_logical_id:
            failures.append(f"{logical_id}: unstable logical table ID")
        indexes = [fragment.get("table_fragment_index") for fragment in fragments]
        if indexes != list(range(1, len(fragments) + 1)):
            failures.append(f"{logical_id}: invalid fragment index sequence")
        if first.get("is_continuation") or first.get("continued_from_block_id"):
            failures.append(f"{logical_id}: first fragment marked as continuation")
        for previous, current in zip(fragments, fragments[1:]):
            if (
                not current.get("is_continuation")
                or current.get("continued_from_block_id") != previous.get("block_id")
            ):
                failures.append(
                    f"{current.get('block_id')}: invalid continuation predecessor"
                )
    return failures


def audit_processed_file(path: str | Path) -> dict:
    path = Path(path)
    blocks = load_jsonl(path)
    tables = _table_blocks(blocks)
    failures = []
    warnings = []
    failures.extend(_validate_table_identities(tables))
    if len({block.get("block_id") for block in blocks}) != len(blocks):
        failures.append("duplicate block IDs")

    measured = []
    bad_financial_titles = []
    density_exceptions = []
    header_coverage_values = []
    fallback_block_ids = []
    row_text_fallback_ids = []
    for block in tables:
        block_id = block.get("block_id", "unknown")
        if block.get("table_schema_version") != TABLE_SCHEMA_VERSION:
            failures.append(f"{block_id}: stale table schema")
            continue
        if block.get("table_heuristics_version") != TABLE_HEURISTICS_VERSION:
            failures.append(f"{block_id}: unknown table heuristics")
        failures.extend(_validate_physical_evidence(block))
        try:
            validate_logical_table(block)
        except (KeyError, TypeError, ValueError) as error:
            failures.append(f"{block_id}: {error}")
            continue
        value = table_quality_metrics(block)
        measured.append((block, value))
        if block.get("header_mode") != "headerless":
            value_columns = [
                index
                for index, column in enumerate(block.get("logical_columns") or [])
                if column.get("role") != "row_label"
            ]
            covered_columns = [
                index
                for index in value_columns
                if block["logical_column_headers"][index]
                and block["logical_column_header_metadata"][index].get(
                    "source_raw_cell_ids"
                )
            ]
            header_coverage_values.append(
                len(covered_columns) / len(value_columns) if value_columns else 1.0
            )
        diagnostics = block.get("normalization_diagnostics") or {}
        if value["raw_cell_accounting_coverage"] != 1.0:
            failures.append(f"{block_id}: incomplete raw-cell accounting")
        if value["standalone_marker_count"]:
            failures.append(f"{block_id}: standalone marker cells")
        if not value["markdown_valid"]:
            failures.append(f"{block_id}: invalid Markdown")
        if diagnostics.get("collisions"):
            failures.append(f"{block_id}: normalization collisions")
        if diagnostics.get("unmapped_nonempty_raw_cell_ids"):
            failures.append(f"{block_id}: unmapped nonempty raw cells")
        if diagnostics.get("fallback_used"):
            fallback_block_ids.append(block_id)
            warnings.append(
                {
                    "block_id": block_id,
                    "type": "normalization_warning",
                    "reasons": diagnostics.get("fallback_reasons") or [],
                }
            )
        if block.get("normalization_mode") == "row_text_fallback":
            row_text_fallback_ids.append(block_id)
            if block.get("table_kind") not in {"layout", "unknown"}:
                failures.append(f"{block_id}: forbidden row-text fallback kind")
        if (
            block.get("table_kind") in QUALITY_DENSITY_KINDS
            and value["logical_empty_density"] > 0.5
        ):
            density_exceptions.append(
                {
                    "block_id": block_id,
                    "logical_table_id": block.get("logical_table_id"),
                    "table_kind": block.get("table_kind"),
                    "logical_empty_density": value["logical_empty_density"],
                }
            )
        title_flags = _title_quality_flags(block)
        if "overlong" in title_flags:
            warnings.append(
                {
                    "block_id": block_id,
                    "type": "overlong_title",
                    "title_length": len(block.get("title") or ""),
                }
            )
        if block.get("table_kind") == "financial_data" and any(
            value in {"company_header", "generic_notes", "page_label"}
            for value in title_flags
        ):
            bad_financial_titles.append(
                {"block_id": block_id, "title": block.get("title"), "flags": title_flags}
            )
            failures.append(f"{block_id}: invalid financial table title")
        if block.get("title_source") == "none":
            warnings.append(
                {
                    "block_id": block_id,
                    "type": "missing_title",
                    "table_kind": block.get("table_kind"),
                }
            )
        if block.get("table_kind") == "unknown":
            warnings.append({"block_id": block_id, "type": "unknown_kind"})

    headers = Counter(block.get("header_mode") for block in tables)
    kinds = Counter(block.get("table_kind") for block in tables)
    classes = Counter(block.get("table_class") for block in tables)
    title_sources = Counter(block.get("title_source") for block in tables)
    title_buckets = Counter(
        TITLE_SOURCE_BUCKETS.get(block.get("title_source"), "other")
        for block in tables
    )
    title_buckets["rejected"] = sum(
        bool(block.get("rejected_title_candidates")) for block in tables
    )
    title_quality = Counter(
        flag for block in tables for flag in _title_quality_flags(block)
    )
    continuation_rejections = Counter(
        reason
        for block in tables
        for reason in block.get("continuation_rejection_reasons") or []
    )
    groups = defaultdict(list)
    for block in tables:
        groups[block.get("logical_table_id")].append(block)
    accepted_links = sum(max(0, len(group) - 1) for group in groups.values())
    candidate_pairs = accepted_links + sum(
        bool(block.get("continuation_rejection_reasons")) for block in tables
    )
    orphan_continued = [
        block["block_id"]
        for block in tables
        if not block.get("is_continuation")
        and has_explicit_continued_cue(
            block.get("title"),
            *(block.get("continuation_cues") or []),
        )
    ]
    if orphan_continued:
        warnings.extend(
            {"block_id": block_id, "type": "orphan_continued_cue"}
            for block_id in orphan_continued
        )

    rejected_caption_candidates = [
        {
            "block_id": block["block_id"],
            "table_kind": block.get("table_kind"),
            "candidate": candidate.get("text"),
            "reason_codes": candidate.get("reason_codes") or [],
        }
        for block in tables
        for candidate in block.get("rejected_title_candidates") or []
        if candidate.get("source") == "prose_caption"
    ]
    warnings.extend(
        {
            "block_id": value["block_id"],
            "type": "rejected_caption_candidate",
            "reasons": value["reason_codes"],
        }
        for value in rejected_caption_candidates
    )

    quality_fragments = [
        (block, value)
        for block, value in measured
        if block.get("table_kind") in QUALITY_DENSITY_KINDS
    ]
    high_density_rate = (
        len(density_exceptions) / len(quality_fragments) if quality_fragments else 0.0
    )
    return {
        "ticker": blocks[0].get("ticker"),
        "filing_year": blocks[0].get("filing_year"),
        "filing_metadata": {
            field: blocks[0].get(field)
            for field in (
                "company",
                "ticker",
                "cik",
                "form",
                "filing_year",
                "filing_date",
                "reporting_period",
                "accession_number",
                "source_url",
            )
        },
        "processed_path": str(path.resolve()),
        "processed_sha256": sha256_file(path),
        "block_count": len(blocks),
        "narrative_block_count": len(blocks) - len(tables),
        "table_fragment_count": len(tables),
        "logical_table_count": len(groups),
        "included_logical_table_count": len(
            {
                block.get("logical_table_id")
                for block in tables
                if block.get("content_type") in INCLUDED_TABLE_CONTENT_TYPES
            }
        ),
        "navigation_logical_table_count": len(
            {
                block.get("logical_table_id")
                for block in tables
                if block.get("content_type") == "navigation"
            }
        ),
        "table_kind_counts": dict(sorted(kinds.items())),
        "table_class_counts": dict(sorted(classes.items())),
        "header_mode_counts": dict(sorted(headers.items())),
        "title_source_counts": dict(sorted(title_sources.items())),
        "title_review_counts": dict(sorted(title_buckets.items())),
        "title_quality_counts": dict(sorted(title_quality.items())),
        "caption_cues": {
            "accepted_count": sum(
                block.get("title_source") == "prose_caption" for block in tables
            ),
            "rejected_count": len(rejected_caption_candidates),
            "rejected_candidates": rejected_caption_candidates,
        },
        "continuation": {
            "candidate_pair_count": candidate_pairs,
            "accepted_link_count": accepted_links,
            "rejected_pair_count": candidate_pairs - accepted_links,
            "rejection_reason_counts": dict(sorted(continuation_rejections.items())),
            "orphan_continued_block_ids": orphan_continued,
        },
        "source_coordinate_width": _distribution(
            block["normalization_diagnostics"]["source_coordinate_width"]
            for block in tables
        ),
        "physical_display_width": _distribution(
            block["normalization_diagnostics"]["physical_display_width"]
            for block in tables
        ),
        "logical_width": _distribution(block["logical_width"] for block in tables),
        "source_coordinate_empty_density": _distribution(
            value["source_coordinate_empty_density"] for _, value in measured
        ),
        "physical_display_empty_density": _distribution(
            value["physical_display_empty_density"] for _, value in measured
        ),
        "logical_empty_density": _distribution(
            value["logical_empty_density"] for _, value in measured
        ),
        "compression": {
            "source_coordinate_to_logical_width_ratio": _distribution(
                value["source_coordinate_to_logical_width_ratio"]
                for _, value in measured
            ),
            "physical_display_to_logical_width_ratio": _distribution(
                value["physical_display_to_logical_width_ratio"]
                for _, value in measured
            ),
            "logical_to_physical_display_fraction": _distribution(
                value["logical_to_physical_display_fraction"]
                for _, value in measured
            ),
        },
        "quality_density": {
            "scope": "fragment diagnostic; release target is measured per logical-table chunk",
            "eligible_fragment_count": len(quality_fragments),
            "over_50_percent_count": len(density_exceptions),
            "over_50_percent_rate": high_density_rate,
            "exceptions": density_exceptions,
        },
        "header_coverage": {
            "eligible_non_headerless_fragment_count": len(header_coverage_values),
            "distribution": _distribution(header_coverage_values),
            "complete_rate": (
                sum(value == 1.0 for value in header_coverage_values)
                / len(header_coverage_values)
                if header_coverage_values
                else 1.0
            ),
        },
        "raw_cell_accounting_min": min(
            (value["raw_cell_accounting_coverage"] for _, value in measured),
            default=1.0,
        ),
        "standalone_marker_count": sum(
            value["standalone_marker_count"] for _, value in measured
        ),
        "normalization_collision_count": sum(
            len((block.get("normalization_diagnostics") or {}).get("collisions") or [])
            for block in tables
        ),
        "unmapped_nonempty_cell_count": sum(
            len(
                (block.get("normalization_diagnostics") or {}).get(
                    "unmapped_nonempty_raw_cell_ids"
                )
                or []
            )
            for block in tables
        ),
        "markdown_validity": (
            sum(value["markdown_valid"] for _, value in measured) / len(measured)
            if measured
            else 1.0
        ),
        "fallback_fragment_count": len(fallback_block_ids),
        "fallback_block_ids": fallback_block_ids,
        "row_text_fallback_count": len(row_text_fallback_ids),
        "row_text_fallback_block_ids": row_text_fallback_ids,
        "unknown_block_ids": [
            block["block_id"] for block in tables if block.get("table_kind") == "unknown"
        ],
        "missing_title_block_ids": [
            block["block_id"] for block in tables if block.get("title_source") == "none"
        ],
        "missing_financial_title_block_ids": [
            block["block_id"]
            for block in tables
            if block.get("table_kind") == "financial_data"
            and block.get("title_source") == "none"
        ],
        "overlong_title_block_ids": [
            block["block_id"]
            for block in tables
            if "overlong" in _title_quality_flags(block)
        ],
        "invalid_financial_titles": bad_financial_titles,
        "warnings": warnings,
        "failures": failures,
    }


def _markdown_subtables(text: str) -> list[str]:
    groups = []
    current = []
    for line in text.splitlines():
        if line.startswith("|"):
            current.append(line)
        elif current:
            groups.append("\n".join(current))
            current = []
    if current:
        groups.append("\n".join(current))
    return groups


def _load_embedding_tokenizer():
    try:
        from transformers import AutoTokenizer
    except ImportError as error:
        raise RuntimeError("transformers is required for embedding-token audit") from error
    config = model_config(DEFAULT_MODEL_NAME)
    return AutoTokenizer.from_pretrained(
        config["repository"], revision=config["revision"]
    )


def _embedding_token_lengths(chunks: list[dict], tokenizer) -> list[int]:
    texts = [
        prepare_document_text(embedding_text(chunk), DEFAULT_MODEL_NAME)
        for chunk in chunks
    ]
    tokenized = tokenizer(
        texts,
        add_special_tokens=True,
        padding=False,
        truncation=False,
        verbose=False,
    )
    return [len(value) for value in tokenized["input_ids"]]


def _logical_empty_density(rows: list[list], roles: list[str], width: int) -> float:
    data_rows = [
        row
        for row, role in zip(rows, roles)
        if role not in {"section_label", "footnote"}
    ]
    slots = len(data_rows) * width
    return sum(not value for row in data_rows for value in row) / slots if slots else 0.0


def _chunk_logical_empty_density(chunk: dict) -> float:
    if chunk.get("composition_mode") != "compound":
        return _logical_empty_density(
            chunk.get("logical_rows") or [],
            chunk.get("logical_row_roles") or [],
            int(chunk.get("logical_width") or 0),
        )
    empty = 0
    slots = 0
    for fragment in chunk.get("logical_fragments") or []:
        width = int(fragment.get("logical_width") or 0)
        for row, role in zip(
            fragment.get("logical_rows") or [],
            fragment.get("logical_row_roles") or [],
        ):
            if role in {"section_label", "footnote"}:
                continue
            slots += width
            empty += sum(not value for value in row)
    return empty / slots if slots else 0.0


def audit_chunk_file(
    path: str | Path,
    *,
    processed_path: str | Path,
    config: dict,
    config_path: str | Path,
    embedding_tokenizer=None,
) -> dict:
    path = Path(path)
    processed_path = Path(processed_path)
    chunks = load_jsonl(path)
    blocks = load_jsonl(processed_path)
    tables = [chunk for chunk in chunks if chunk.get("content_type") == "table"]
    failures = []
    try:
        validate_chunks(chunks, config)
    except (KeyError, TypeError, ValueError) as error:
        failures.append(str(error))

    expected_source_hash = sha256_file(processed_path)
    expected_config_hash = sha256_file(config_path)
    if {chunk.get("source_processed_sha256") for chunk in chunks} != {
        expected_source_hash
    }:
        failures.append("chunk source-processed hash does not match processed file")
    if {chunk.get("chunking_config_sha256") for chunk in chunks} != {
        expected_config_hash
    }:
        failures.append("chunk config hash does not match checked-in config")

    processed_tables = _table_blocks(blocks)
    expected_groups = defaultdict(list)
    excluded_ids = set()
    for block in processed_tables:
        if block.get("content_type") in INCLUDED_TABLE_CONTENT_TYPES:
            expected_groups[block["logical_table_id"]].append(block["block_id"])
        else:
            excluded_ids.add(block["logical_table_id"])
    actual_groups = {
        chunk.get("logical_table_id"): list(chunk.get("fragment_block_ids") or [])
        for chunk in tables
    }
    if set(actual_groups) != set(expected_groups):
        failures.append("included logical table IDs do not map one-to-one to chunks")
    if excluded_ids & set(actual_groups):
        failures.append("navigation logical table produced a chunk")
    for logical_id, expected_blocks in expected_groups.items():
        if actual_groups.get(logical_id) != expected_blocks:
            failures.append(f"{logical_id}: fragment block provenance mismatch")

    for chunk in tables:
        groups = _markdown_subtables(chunk.get("text", ""))
        if not groups or not all(validate_markdown(group) for group in groups):
            failures.append(f"{chunk.get('chunk_id')}: invalid Markdown")

    chunk_tokenizer = get_tokenizer(config)
    chunk_token_lengths = [
        count_tokens(chunk["text"], chunk_tokenizer) for chunk in tables
    ]
    embedding_lengths = None
    truncated = []
    if embedding_tokenizer is not None:
        embedding_lengths = _embedding_token_lengths(chunks, embedding_tokenizer)
        truncated = [
            {
                "chunk_id": chunk["chunk_id"],
                "content_type": chunk["content_type"],
                "table_kind": chunk.get("table_kind"),
                "logical_table_id": chunk.get("logical_table_id"),
                "token_count": length,
            }
            for chunk, length in zip(chunks, embedding_lengths)
            if length > MAX_EMBEDDING_TOKENS
        ]

    covered_blocks = {
        block_id for chunk in chunks for block_id in chunk.get("block_ids") or []
    }
    required_blocks = {
        block["block_id"]
        for block in blocks
        if block.get("content_type") not in {"heading", "navigation"}
    }
    covered_anchors = {
        anchor
        for chunk in chunks
        for anchor in chunk.get("source_anchors") or []
        if anchor
    }
    required_anchors = {
        block.get("source_anchor")
        for block in blocks
        if block.get("content_type") not in {"heading", "navigation"}
        and block.get("source_anchor")
    }
    missing_blocks = required_blocks - covered_blocks
    missing_anchors = required_anchors - covered_anchors
    if missing_blocks:
        failures.append("chunk provenance omits non-heading source blocks")
    if missing_anchors:
        failures.append("chunk provenance omits non-heading source anchors")
    density_values = [
        {
            "chunk_id": chunk["chunk_id"],
            "logical_table_id": chunk["logical_table_id"],
            "table_kind": chunk.get("table_kind"),
            "logical_empty_density": _chunk_logical_empty_density(chunk),
        }
        for chunk in tables
        if chunk.get("table_kind") in QUALITY_DENSITY_KINDS
    ]
    density_exceptions = [
        value for value in density_values if value["logical_empty_density"] > 0.5
    ]
    return {
        "ticker": chunks[0].get("ticker"),
        "chunk_path": str(path.resolve()),
        "chunk_sha256": sha256_file(path),
        "source_processed_sha256": expected_source_hash,
        "chunking_config_sha256": expected_config_hash,
        "chunk_count": len(chunks),
        "narrative_chunk_count": len(chunks) - len(tables),
        "table_chunk_count": len(tables),
        "logical_table_count": len(actual_groups),
        "table_fragment_count": sum(
            len(chunk.get("fragment_block_ids") or []) for chunk in tables
        ),
        "composition_mode_counts": dict(
            sorted(Counter(chunk.get("composition_mode") for chunk in tables).items())
        ),
        "quality_density": {
            "eligible_logical_table_count": len(density_values),
            "over_50_percent_count": len(density_exceptions),
            "over_50_percent_rate": (
                len(density_exceptions) / len(density_values)
                if density_values
                else 0.0
            ),
            "exceptions": density_exceptions,
        },
        "horizontal_logical_table_ids": [
            chunk["logical_table_id"]
            for chunk in tables
            if chunk.get("composition_mode") == "horizontal"
        ],
        "compound_logical_table_ids": [
            chunk["logical_table_id"]
            for chunk in tables
            if chunk.get("composition_mode") == "compound"
        ],
        "vertical_logical_table_ids": [
            chunk["logical_table_id"]
            for chunk in tables
            if chunk.get("composition_mode") == "vertical"
        ],
        "table_token_length": _distribution(chunk_token_lengths),
        "embedding_input_token_length": (
            _distribution(embedding_lengths) if embedding_lengths is not None else None
        ),
        "embedding_input_limit": MAX_EMBEDDING_TOKENS,
        "truncated_input_count": len(truncated),
        "truncated_table_count": sum(
            value["content_type"] == "table" for value in truncated
        ),
        "truncated_narrative_count": sum(
            value["content_type"] == "narrative" for value in truncated
        ),
        "truncated_inputs": truncated,
        "source_block_coverage": (
            len(required_blocks & covered_blocks) / len(required_blocks)
            if required_blocks
            else 1.0
        ),
        "source_anchor_coverage": (
            len(required_anchors & covered_anchors) / len(required_anchors)
            if required_anchors
            else 1.0
        ),
        "source_coverage_scope": "non-heading non-navigation processed blocks",
        "missing_source_block_ids": sorted(missing_blocks),
        "missing_source_anchors": sorted(missing_anchors),
        "failures": failures,
    }


def _legacy_processed_summary(path: Path) -> dict:
    blocks = load_jsonl(path)
    tables = _table_blocks(blocks)
    widths = [max((len(row) for row in block.get("rows") or []), default=0) for block in tables]
    densities = []
    markers = 0
    for block, width in zip(tables, widths):
        rows = block.get("rows") or []
        slots = len(rows) * width
        nonempty = sum(bool(value) for row in rows for value in row)
        densities.append(1 - nonempty / slots if slots else 0.0)
        markers += sum(
            value.strip() in CURRENCY_OR_PERCENT_MARKERS
            for row in rows
            for value in row
        )
    return {
        "processed_path": str(path.resolve()),
        "processed_sha256": sha256_file(path),
        "block_count": len(blocks),
        "narrative_block_count": len(blocks) - len(tables),
        "table_fragment_count": len(tables),
        "logical_table_count": None,
        "table_class_counts": dict(
            sorted(Counter(block.get("table_class") for block in tables).items())
        ),
        "table_kind_counts": None,
        "source_coordinate_width": None,
        "physical_display_width": _distribution(widths),
        "logical_width": None,
        "source_coordinate_empty_density": None,
        "physical_display_empty_density": _distribution(densities),
        "logical_empty_density": None,
        "standalone_marker_count": markers,
        "normalization_collision_count": None,
        "unmapped_nonempty_cell_count": None,
        "raw_cell_accounting_min": None,
        "header_mode_counts": None,
        "title_review_counts": None,
        "continuation": None,
    }


def _legacy_chunk_summary(path: Path) -> dict:
    chunks = load_jsonl(path)
    tables = [chunk for chunk in chunks if chunk.get("content_type") == "table"]
    return {
        "chunk_path": str(path.resolve()),
        "chunk_sha256": sha256_file(path),
        "chunk_count": len(chunks),
        "narrative_chunk_count": len(chunks) - len(tables),
        "table_chunk_count": len(tables),
        "logical_table_count": None,
        "table_token_length": None,
        "source_block_coverage": None,
        "source_anchor_coverage": None,
    }


def _boundary_canonical(text: str) -> str:
    value = unicodedata.normalize("NFC", text).replace("\u00ad", "")
    value = value.replace("\u200b", "").replace("\ufeff", "")
    return "".join(value.split())


def compare_narrative_artifacts(
    old_processed_path: Path,
    new_processed_path: Path,
    old_chunk_path: Path | None,
    new_chunk_path: Path | None,
) -> dict:
    old_blocks = [
        block
        for block in load_jsonl(old_processed_path)
        if block.get("content_type") in NARRATIVE_CONTENT_TYPES
    ]
    new_blocks = [
        block
        for block in load_jsonl(new_processed_path)
        if block.get("content_type") in NARRATIVE_CONTENT_TYPES
    ]
    old_ids = [block["block_id"] for block in old_blocks]
    new_ids = [block["block_id"] for block in new_blocks]
    failures = []
    if old_ids != new_ids:
        failures.append("narrative block identity/order changed")
    old_by_id = {block["block_id"]: block for block in old_blocks}
    new_by_id = {block["block_id"]: block for block in new_blocks}
    changes = []
    for block_id in old_ids:
        old = old_by_id[block_id]
        new = new_by_id.get(block_id)
        if new is None:
            continue
        if old.get("content_type") != new.get("content_type"):
            failures.append(f"{block_id}: narrative content type changed")
        if old.get("section") != new.get("section") or old.get("section_path") != new.get("section_path"):
            failures.append(f"{block_id}: narrative section/path changed")
        if old.get("text") == new.get("text"):
            continue
        approved = _boundary_canonical(old.get("text", "")) == _boundary_canonical(
            new.get("text", "")
        )
        if not approved:
            failures.append(f"{block_id}: narrative text changed beyond boundary rules")
        changes.append(
            {
                "block_id": block_id,
                "reason": (
                    "approved_visible_text_boundary_repair"
                    if approved
                    else "unexplained_change"
                ),
                "old_sha256": sha256_text(old.get("text", "")),
                "new_sha256": sha256_text(new.get("text", "")),
                "old_excerpt": old.get("text", "")[:240],
                "new_excerpt": new.get("text", "")[:240],
            }
        )

    chunk_comparison = None
    if old_chunk_path and new_chunk_path:
        old_chunks = [
            chunk
            for chunk in load_jsonl(old_chunk_path)
            if chunk.get("content_type") == "narrative"
        ]
        new_chunks = [
            chunk
            for chunk in load_jsonl(new_chunk_path)
            if chunk.get("content_type") == "narrative"
        ]
        exact = sum(
            old.get("text") == new.get("text")
            for old, new in zip(old_chunks, new_chunks)
        )
        boundary_equivalent = sum(
            _boundary_canonical(old.get("text", ""))
            == _boundary_canonical(new.get("text", ""))
            for old, new in zip(old_chunks, new_chunks)
        )
        chunk_comparison = {
            "old_count": len(old_chunks),
            "new_count": len(new_chunks),
            "positionally_exact_count": exact,
            "positionally_boundary_equivalent_count": boundary_equivalent,
            "historical_configuration_compatible": len(old_chunks) == len(new_chunks),
        }

    return {
        "old_block_count": len(old_blocks),
        "new_block_count": len(new_blocks),
        "order_and_identity_preserved": old_ids == new_ids,
        "unchanged_block_count": len(old_blocks) - len(changes),
        "changed_block_count": len(changes),
        "changed_blocks": changes,
        "chunk_comparison": chunk_comparison,
        "failures": failures,
    }


def _paths_by_ticker(directory: Path, pattern: str) -> dict[str, Path]:
    paths = {}
    for path in sorted(directory.glob(pattern)):
        ticker = path.parent.name
        if ticker in paths:
            raise ValueError(f"Multiple artifacts found for {ticker}: {directory}")
        paths[ticker] = path
    return paths


def _load_input_manifest(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    files = value.get("files") or []
    company_count = value.get("company_count")
    if (
        value.get("schema_version") != 1
        or not isinstance(company_count, int)
        or company_count < 1
        or len(files) != company_count
    ):
        raise ValueError("Input manifest company_count must match its filing records")
    return value


def _validate_inputs(input_manifest: dict) -> tuple[list[dict], list[str]]:
    records = []
    failures = []
    for record in input_manifest["files"]:
        raw_path = PROJECT_ROOT / record["filing"]
        actual_hash = sha256_file(raw_path, prefixed=True)
        status = "valid" if actual_hash == record["raw_sha256"] else "hash_mismatch"
        if status != "valid":
            failures.append(f"{record['ticker']}: frozen raw hash mismatch")
        records.append(
            {
                **record,
                "resolved_path": str(raw_path.resolve()),
                "actual_raw_sha256": actual_hash,
                "status": status,
            }
        )
    return records, failures


def _review_coverage(report: dict, path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    reviewed = defaultdict(set)
    invalid_entries = []
    for entry in manifest.get("reviews") or []:
        if entry.get("decision") not in {"approved", "rejected"}:
            invalid_entries.append(entry.get("id"))
        for review_type in entry.get("review_types") or []:
            for value in entry.get("block_ids") or []:
                reviewed[review_type].add(value)
            for value in entry.get("logical_table_ids") or []:
                reviewed[review_type].add(value)
            for value in entry.get("chunk_ids") or []:
                reviewed[review_type].add(value)

    required = defaultdict(set)
    for processed in report["processed"]:
        required["normalization_warning"].update(processed["fallback_block_ids"])
        required["unknown_kind"].update(processed["unknown_block_ids"])
        required["missing_financial_title"].update(
            processed["missing_financial_title_block_ids"]
        )
        required["rejected_caption_candidate"].update(
            value["block_id"]
            for value in processed["caption_cues"]["rejected_candidates"]
        )
        required["orphan_continued_cue"].update(
            processed["continuation"]["orphan_continued_block_ids"]
        )
        required["overlong_title"].update(processed["overlong_title_block_ids"])
    for chunks in report["chunks"]:
        required["horizontal_composition"].update(
            chunks["horizontal_logical_table_ids"]
        )
        required["compound_composition"].update(
            chunks["compound_logical_table_ids"]
        )
        required["density_exception"].update(
            value["chunk_id"] for value in chunks["quality_density"]["exceptions"]
        )
        required["oversized_embedding_input"].update(
            value["chunk_id"] for value in chunks["truncated_inputs"]
        )
    missing = {
        review_type: sorted(values - reviewed[review_type])
        for review_type, values in required.items()
        if values - reviewed[review_type]
    }
    return {
        "manifest_path": str(path.resolve()),
        "manifest_sha256": sha256_file(path),
        "required_counts": {
            review_type: len(values) for review_type, values in sorted(required.items())
        },
        "reviewed_counts": {
            review_type: len(reviewed[review_type])
            for review_type in sorted(required)
        },
        "missing": missing,
        "invalid_entry_ids": invalid_entries,
        "complete": not missing and not invalid_entries,
    }


def audit_corpus(
    processed_directory: str | Path,
    chunks_directory: str | Path | None = None,
    *,
    config_path: str | Path = PROJECT_ROOT / "data" / "chunks" / "chunking-config.json",
    input_manifest_path: str | Path | None = None,
    baseline_processed_directory: str | Path | None = None,
    baseline_chunks_directory: str | Path | None = None,
    measure_embedding_tokens: bool = False,
    review_decisions_path: str | Path | None = None,
) -> dict:
    processed_directory = Path(processed_directory)
    config_path = Path(config_path)
    input_manifest = (
        _load_input_manifest(Path(input_manifest_path))
        if input_manifest_path is not None
        else None
    )
    expected_company_count = (
        input_manifest["company_count"]
        if input_manifest is not None
        else ACTIVE_COMPANY_COUNT
    )
    processed_paths = _paths_by_ticker(
        processed_directory, "*/*-10-K.blocks.jsonl"
    )
    processed = [
        audit_processed_file(processed_paths[ticker]) for ticker in sorted(processed_paths)
    ]
    processed_by_ticker = {value["ticker"]: value for value in processed}
    failures = [
        f"{value['ticker']}: {failure}"
        for value in processed
        for failure in value["failures"]
    ]
    if len(processed_paths) != expected_company_count:
        failures.append(
            f"expected {expected_company_count} processed filings, "
            f"found {len(processed_paths)}"
        )

    chunks = []
    chunk_paths = {}
    if chunks_directory is not None:
        chunks_directory = Path(chunks_directory)
        chunk_paths = _paths_by_ticker(chunks_directory, "*/*-10-K.chunks.jsonl")
        config = load_chunk_config(config_path)
        tokenizer = _load_embedding_tokenizer() if measure_embedding_tokens else None
        for ticker in sorted(chunk_paths):
            if ticker not in processed_paths:
                failures.append(f"{ticker}: chunk file has no processed source")
                continue
            value = audit_chunk_file(
                chunk_paths[ticker],
                processed_path=processed_paths[ticker],
                config=config,
                config_path=config_path,
                embedding_tokenizer=tokenizer,
            )
            chunks.append(value)
            failures.extend(f"{ticker}: {failure}" for failure in value["failures"])
        if len(chunk_paths) != expected_company_count:
            failures.append(
                f"expected {expected_company_count} chunk files, found {len(chunk_paths)}"
            )

    input_records = []
    if input_manifest is not None:
        input_records, input_failures = _validate_inputs(input_manifest)
        failures.extend(input_failures)
        expected_tickers = {value["ticker"] for value in input_records}
        if set(processed_paths) != expected_tickers:
            failures.append("processed corpus does not match approved input manifest")
        for record in input_records:
            actual = processed_by_ticker.get(record["ticker"])
            if actual and any(
                actual["filing_metadata"].get(field) != record.get(field)
                for field in (
                    "filing_year",
                    "filing_date",
                    "reporting_period",
                    "accession_number",
                    "source_url",
                )
            ):
                failures.append(
                    f"{record['ticker']}: processed metadata differs from input manifest"
                )

    comparison = None
    if baseline_processed_directory is not None:
        old_processed_paths = _paths_by_ticker(
            Path(baseline_processed_directory), "*/*-10-K.blocks.jsonl"
        )
        old_chunk_paths = (
            _paths_by_ticker(
                Path(baseline_chunks_directory), "*/*-10-K.chunks.jsonl"
            )
            if baseline_chunks_directory is not None
            else {}
        )
        new_chunks_by_ticker = {value["ticker"]: value for value in chunks}
        rows = []
        for ticker in sorted(processed_paths):
            if ticker not in old_processed_paths:
                failures.append(f"{ticker}: no historical processed artifact")
                continue
            old_processed = _legacy_processed_summary(old_processed_paths[ticker])
            old_chunks = (
                _legacy_chunk_summary(old_chunk_paths[ticker])
                if ticker in old_chunk_paths
                else None
            )
            narrative = compare_narrative_artifacts(
                old_processed_paths[ticker],
                processed_paths[ticker],
                old_chunk_paths.get(ticker),
                chunk_paths.get(ticker),
            )
            failures.extend(
                f"{ticker}: {failure}" for failure in narrative["failures"]
            )
            rows.append(
                {
                    "ticker": ticker,
                    "filing_metadata": processed_by_ticker[ticker]["filing_metadata"],
                    "raw_sha256": next(
                        (
                            value["actual_raw_sha256"]
                            for value in input_records
                            if value["ticker"] == ticker
                        ),
                        None,
                    ),
                    "old": {"processed": old_processed, "chunks": old_chunks},
                    "new": {
                        "processed": processed_by_ticker[ticker],
                        "chunks": new_chunks_by_ticker.get(ticker),
                    },
                    "narrative_regression": narrative,
                }
            )
        comparison = {
            "baseline_processed_directory": str(
                Path(baseline_processed_directory).resolve()
            ),
            "baseline_chunks_directory": (
                str(Path(baseline_chunks_directory).resolve())
                if baseline_chunks_directory is not None
                else None
            ),
            "rows": rows,
            "totals": {
                "old": {
                    "block_count": sum(
                        row["old"]["processed"]["block_count"] for row in rows
                    ),
                    "narrative_block_count": sum(
                        row["old"]["processed"]["narrative_block_count"]
                        for row in rows
                    ),
                    "table_fragment_count": sum(
                        row["old"]["processed"]["table_fragment_count"]
                        for row in rows
                    ),
                    "chunk_count": sum(
                        (row["old"]["chunks"] or {}).get("chunk_count", 0)
                        for row in rows
                    ),
                    "narrative_chunk_count": sum(
                        (row["old"]["chunks"] or {}).get(
                            "narrative_chunk_count", 0
                        )
                        for row in rows
                    ),
                    "table_chunk_count": sum(
                        (row["old"]["chunks"] or {}).get("table_chunk_count", 0)
                        for row in rows
                    ),
                },
                "new": {
                    "block_count": sum(row["new"]["processed"]["block_count"] for row in rows),
                    "narrative_block_count": sum(
                        row["new"]["processed"]["narrative_block_count"]
                        for row in rows
                    ),
                    "table_fragment_count": sum(
                        row["new"]["processed"]["table_fragment_count"]
                        for row in rows
                    ),
                    "logical_table_count": sum(
                        row["new"]["processed"]["logical_table_count"]
                        for row in rows
                    ),
                    "chunk_count": sum(
                        (row["new"]["chunks"] or {}).get("chunk_count", 0)
                        for row in rows
                    ),
                    "narrative_chunk_count": sum(
                        (row["new"]["chunks"] or {}).get(
                            "narrative_chunk_count", 0
                        )
                        for row in rows
                    ),
                    "table_chunk_count": sum(
                        (row["new"]["chunks"] or {}).get("table_chunk_count", 0)
                        for row in rows
                    ),
                },
            },
        }

    report = {
        "report_schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "table_schema_version": TABLE_SCHEMA_VERSION,
        "chunk_schema_version": CHUNK_SCHEMA_VERSION,
        "table_heuristics_version": TABLE_HEURISTICS_VERSION,
        "company_count": len(processed),
        "input_manifest": input_records,
        "processed": processed,
        "chunks": chunks,
        "comparison": comparison,
        "aggregate": {
            "block_count": sum(value["block_count"] for value in processed),
            "narrative_block_count": sum(
                value["narrative_block_count"] for value in processed
            ),
            "table_fragment_count": sum(
                value["table_fragment_count"] for value in processed
            ),
            "logical_table_count": sum(
                value["logical_table_count"] for value in processed
            ),
            "included_logical_table_count": sum(
                value["included_logical_table_count"] for value in processed
            ),
            "navigation_logical_table_count": sum(
                value["navigation_logical_table_count"] for value in processed
            ),
            "chunk_count": sum(value["chunk_count"] for value in chunks),
            "narrative_chunk_count": sum(
                value["narrative_chunk_count"] for value in chunks
            ),
            "table_chunk_count": sum(value["table_chunk_count"] for value in chunks),
            "fallback_fragment_count": sum(
                value["fallback_fragment_count"] for value in processed
            ),
            "row_text_fallback_count": sum(
                value["row_text_fallback_count"] for value in processed
            ),
            "standalone_marker_count": sum(
                value["standalone_marker_count"] for value in processed
            ),
            "normalization_collision_count": sum(
                value["normalization_collision_count"] for value in processed
            ),
            "unmapped_nonempty_cell_count": sum(
                value["unmapped_nonempty_cell_count"] for value in processed
            ),
            "invalid_financial_title_count": sum(
                len(value["invalid_financial_titles"]) for value in processed
            ),
            "truncated_input_count": sum(
                value["truncated_input_count"] for value in chunks
            ),
            "truncated_table_count": sum(
                value["truncated_table_count"] for value in chunks
            ),
            "truncated_narrative_count": sum(
                value["truncated_narrative_count"] for value in chunks
            ),
        },
        "failures": failures,
    }
    eligible_quality_tables = sum(
        value["quality_density"]["eligible_logical_table_count"] for value in chunks
    )
    quality_exceptions = sum(
        value["quality_density"]["over_50_percent_count"] for value in chunks
    )
    report["aggregate"]["quality_density"] = {
        "eligible_logical_table_count": eligible_quality_tables,
        "over_50_percent_count": quality_exceptions,
        "over_50_percent_rate": (
            quality_exceptions / eligible_quality_tables
            if eligible_quality_tables
            else 0.0
        ),
    }
    if (
        chunks
        and eligible_quality_tables
        and quality_exceptions / eligible_quality_tables > 0.05
    ):
        report["failures"].append(
            "financial/structured logical empty-density exception rate exceeds 5%"
        )
    if review_decisions_path is not None:
        review = _review_coverage(report, Path(review_decisions_path))
        report["manual_review"] = review
        if not review["complete"]:
            report["failures"].append("manual review coverage is incomplete")
    report["aggregate"]["failure_count"] = len(report["failures"])
    return report


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit logical SEC table artifacts without modifying them."
    )
    parser.add_argument(
        "--processed-directory",
        type=Path,
        default=PROJECT_ROOT / "data" / "processed",
    )
    parser.add_argument("--chunks-directory", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "data" / "chunks" / "chunking-config.json",
    )
    parser.add_argument("--input-manifest", type=Path)
    parser.add_argument("--baseline-processed-directory", type=Path)
    parser.add_argument("--baseline-chunks-directory", type=Path)
    parser.add_argument("--measure-embedding-tokens", action="store_true")
    parser.add_argument("--review-decisions", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    arguments = parser.parse_args()
    report = audit_corpus(
        arguments.processed_directory,
        arguments.chunks_directory,
        config_path=arguments.config,
        input_manifest_path=arguments.input_manifest,
        baseline_processed_directory=arguments.baseline_processed_directory,
        baseline_chunks_directory=arguments.baseline_chunks_directory,
        measure_embedding_tokens=arguments.measure_embedding_tokens,
        review_decisions_path=arguments.review_decisions,
    )
    if arguments.output:
        write_json_atomic(arguments.output, report)
        print(f"Wrote table audit to {arguments.output}")
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    if arguments.strict and report["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
