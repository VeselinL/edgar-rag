import argparse
import bisect
import copy
import hashlib
import json
import math
import os
import re
import statistics
import tempfile
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.filings.fetch_data import COMPANIES
from src.filings.release_state import assert_release_available
from src.filings.table_processing import (
    TABLE_HEURISTICS_VERSION,
    TABLE_SCHEMA_VERSION,
    render_logical_table,
    validate_logical_table,
    validate_markdown,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "data" / "chunks" / "chunking-config.json"
DEFAULT_PROCESSED_DIRECTORY = PROJECT_ROOT / "data" / "processed"
CHUNK_SCHEMA_VERSION = 3

METADATA_FIELDS = (
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
    "source_url",
)


def load_jsonl(path: str | Path) -> list[dict]:
    assert_release_available(path)
    blocks = []
    with Path(path).open(encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected an object on line {line_number}: {path}")
            blocks.append(value)
    if not blocks:
        raise ValueError(f"No blocks found in {path}")
    return blocks


def find_latest_processed_filing(
    company_name: str,
    processed_directory: str | Path = DEFAULT_PROCESSED_DIRECTORY,
) -> Path:
    """Return the latest processed 10-K blocks file for a configured company."""
    assert_release_available(processed_directory)
    company_key = company_name.strip().lower()
    if company_key not in COMPANIES:
        raise ValueError(f"Unknown company: {company_name}")

    ticker = COMPANIES[company_key]["ticker"]
    company_directory = Path(processed_directory) / ticker
    filing_paths = list(company_directory.glob("*-10-K.blocks.jsonl"))
    if not filing_paths:
        raise FileNotFoundError(
            f"No processed 10-K blocks found for {company_name} in "
            f"{company_directory}"
        )

    def filing_year(path: Path) -> int:
        try:
            return int(path.name.split("-", maxsplit=1)[0])
        except ValueError as exc:
            raise ValueError(f"Invalid processed filing filename: {path.name}") from exc

    return max(filing_paths, key=filing_year)


def load_chunk_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    with Path(path).open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    validate_config(config)
    return config


def validate_config(config: dict) -> None:
    if config.get("schema_version") != CHUNK_SCHEMA_VERSION:
        raise ValueError("chunking config schema_version must be 3")
    size = int(config["chunk_size"])
    overlap = int(config["chunk_overlap"])
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")
    if config.get("strategy", "recursive") not in {"recursive", "fixed"}:
        raise ValueError("strategy must be 'recursive' or 'fixed'")
    if config.get("length_function") != "tokens":
        raise ValueError("length_function must be 'tokens'")
    if not config.get("tokenizer_model"):
        raise ValueError("tokenizer_model is required")
    if config.get("table_schema_version") != TABLE_SCHEMA_VERSION:
        raise ValueError("chunking config must require table schema version 2")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as input_file:
        for value in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(value)
    return digest.hexdigest()


def config_sha256(config: dict) -> str:
    return sha256_bytes(
        json.dumps(
            config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def blocks_sha256(blocks: list[dict]) -> str:
    payload = "".join(
        json.dumps(block, ensure_ascii=False, separators=(",", ":")) + "\n"
        for block in blocks
    )
    return sha256_bytes(payload.encode("utf-8"))


@lru_cache(maxsize=None)
def _load_tokenizer(model_name: str, revision: str | None):
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "transformers is required; install dependencies with "
            "'pip install -r requirements.txt'"
        ) from exc
    return AutoTokenizer.from_pretrained(model_name, revision=revision)


def get_tokenizer(config: dict):
    return _load_tokenizer(
        config["tokenizer_model"], config.get("tokenizer_revision")
    )


def count_tokens(text: str, tokenizer) -> int:
    return len(tokenizer.encode(text, add_special_tokens=False, verbose=False))


def recursive_spans(
    text: str,
    size: int,
    overlap: int,
    separators: list[str],
    tokenizer,
):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=separators,
        length_function=lambda value: count_tokens(value, tokenizer),
        strip_whitespace=True,
    )
    search_start = 0
    for content in splitter.split_text(text):
        start = text.find(content, search_start)
        if start < 0:
            raise ValueError("Could not locate a generated chunk in its source text")
        yield content, start, start + len(content)
        search_start = start + 1


def fixed_spans(text: str, size: int, overlap: int, tokenizer):
    offsets = tokenizer(
        text,
        add_special_tokens=False,
        return_offsets_mapping=True,
        verbose=False,
    )["offset_mapping"]
    offsets = [(start, end) for start, end in offsets if end > start]
    token_start = 0
    while token_start < len(offsets):
        token_end = min(len(offsets), token_start + size)
        start = offsets[token_start][0]
        while True:
            end = offsets[token_end - 1][1]
            raw_chunk = text[start:end]
            content = raw_chunk.strip()
            if count_tokens(content, tokenizer) <= size:
                break
            token_end -= 1
        if content:
            content_start = start + len(raw_chunk) - len(raw_chunk.lstrip())
            yield content, content_start, content_start + len(content)
        if token_end == len(offsets):
            break
        token_start = token_end - overlap


def split_spans(text: str, size: int, config: dict, tokenizer):
    if size <= 0:
        raise ValueError("Chunk context exceeds the configured chunk size")
    overlap = min(int(config["chunk_overlap"]), size - 1)
    if config.get("strategy", "recursive") == "fixed":
        return list(fixed_spans(text, size, overlap, tokenizer))
    return list(
        recursive_spans(text, size, overlap, config["separators"], tokenizer)
    )


def unique(values) -> list:
    return list(dict.fromkeys(value for value in values if value is not None))


def source_metadata(blocks: list[dict]) -> dict:
    first = blocks[0]
    metadata = {field: first.get(field) for field in METADATA_FIELDS}
    metadata["block_ids"] = unique(block.get("block_id") for block in blocks)
    metadata["source_anchors"] = unique(
        block.get("source_anchor") for block in blocks
    )
    metadata["source_anchor"] = (
        metadata["source_anchors"][0] if metadata["source_anchors"] else None
    )
    starts = [block["page_start"] for block in blocks if block.get("page_start")]
    ends = [block["page_end"] for block in blocks if block.get("page_end")]
    metadata["page_start"] = min(starts) if starts else None
    metadata["page_end"] = max(ends) if ends else None
    return metadata


def section_prefix(block: dict) -> str:
    return "\n".join(block.get("section_path") or [block["section"]])


def chunk_narrative(
    blocks: list[dict], config: dict, group_id: str, tokenizer
) -> list[dict]:
    headings = [block for block in blocks if block["content_type"] == "heading"]
    body_blocks = [block for block in blocks if block["content_type"] != "heading"]
    if not body_blocks:
        return []

    parts = []
    block_spans = []
    cursor = 0
    for block in body_blocks:
        text = block["text"].strip()
        if block["content_type"] == "list_item":
            text = f"- {text}"
        if parts:
            parts.append("\n\n")
            cursor += 2
        start = cursor
        parts.append(text)
        cursor += len(text)
        block_spans.append((start, cursor, block))

    body = "".join(parts)
    prefix = section_prefix(blocks[0])
    body_size = config["chunk_size"] - count_tokens(prefix, tokenizer)
    spans = split_spans(body, body_size, config, tokenizer)
    token_offsets = tokenizer(
        body,
        add_special_tokens=False,
        return_offsets_mapping=True,
        verbose=False,
    )["offset_mapping"]
    token_offsets = [(start, end) for start, end in token_offsets if end > start]
    token_starts = [start for start, _ in token_offsets]
    token_ends = [end for _, end in token_offsets]
    chunks = []
    for text, start, end in spans:
        contributors = headings + [
            block
            for block_start, block_end, block in block_spans
            if block_end > start and block_start < end
        ]
        chunks.append(
            {
                **source_metadata(contributors),
                "content_type": "narrative",
                "source_group": group_id,
                "source_text_start": start,
                "source_text_end": end,
                "source_token_start": bisect.bisect_right(token_ends, start),
                "source_token_end": bisect.bisect_left(token_starts, end),
                "text": f"{prefix}\n\n{text}",
            }
        )
    return chunks


def render_row(row: list) -> str:
    return " | ".join("" if value is None else str(value).strip() for value in row).rstrip()


LOGICAL_FRAGMENT_FIELDS = (
    "table_schema_version",
    "table_heuristics_version",
    "logical_table_id",
    "table_class",
    "table_kind",
    "document_region",
    "effective_section_path",
    "block_id",
    "html_table_id",
    "html_table_index",
    "html_table_xpath",
    "html_table_fingerprint",
    "html_table_fingerprint_version",
    "table_fragment_index",
    "source_anchor",
    "title",
    "title_source",
    "title_source_block_id",
    "title_source_raw_cell_ids",
    "title_source_locator",
    "title_confidence",
    "title_quality_status",
    "rejected_title_candidates",
    "units",
    "header_mode",
    "header_source_block_id",
    "is_continuation",
    "continued_from_block_id",
    "continuation_reasons",
    "continuation_rejection_reasons",
    "native_context",
    "inherited_context",
    "logical_width",
    "logical_header_rows",
    "logical_header_paths",
    "logical_header_context",
    "logical_header_context_source_raw_cell_ids",
    "logical_column_headers",
    "logical_column_header_metadata",
    "logical_column_units",
    "logical_column_unit_metadata",
    "logical_columns",
    "logical_rows",
    "logical_row_source_indexes",
    "logical_row_roles",
    "logical_cell_states",
    "logical_cell_sources",
    "logical_body_rowspans",
    "normalization_diagnostics",
)


def validate_current_table_block(block: dict) -> None:
    block_id = block.get("block_id", "unknown")
    if block.get("table_schema_version") != TABLE_SCHEMA_VERSION:
        raise ValueError(f"Table block {block_id} does not use table schema version 2")
    if block.get("table_heuristics_version") != TABLE_HEURISTICS_VERSION:
        raise ValueError(f"Table block {block_id} has an unknown heuristics version")
    for field in (
        "logical_table_id",
        "logical_width",
        "logical_header_rows",
        "logical_header_paths",
        "logical_header_context",
        "logical_rows",
        "logical_row_source_indexes",
        "logical_row_roles",
        "logical_column_headers",
        "logical_column_header_metadata",
        "logical_column_units",
        "logical_column_unit_metadata",
        "logical_columns",
        "logical_cell_sources",
        "logical_cell_states",
        "logical_body_rowspans",
        "normalization_diagnostics",
        "raw_cells",
        "html_table_id",
        "table_fragment_index",
    ):
        if field not in block:
            raise ValueError(f"Table block {block_id} lacks logical field {field}")
    validate_logical_table(block, strict=True)


def _cell_source_entry(block: dict, row_index: int, column: int) -> list[dict]:
    raw_ids = block["logical_cell_sources"][row_index][column]
    if not raw_ids:
        return []
    return [
        {
            "source_block_id": block["block_id"],
            "html_table_id": block["html_table_id"],
            "fragment_index": block["table_fragment_index"],
            "raw_cell_ids": raw_ids,
            "source_row_index": block["logical_row_source_indexes"][row_index],
        }
    ]


def _fragment_payload(block: dict) -> dict:
    payload = {field: copy.deepcopy(block.get(field)) for field in LOGICAL_FRAGMENT_FIELDS}
    payload["source_raw_cell_ids"] = [
        cell["raw_cell_id"] for cell in block.get("raw_cells") or []
    ]
    payload["logical_cell_sources"] = [
        [
            _cell_source_entry(block, row_index, column)
            for column in range(block["logical_width"])
        ]
        for row_index in range(len(block["logical_rows"]))
    ]
    payload["logical_row_sources"] = [
        [
            {
                "source_block_id": block["block_id"],
                "html_table_id": block["html_table_id"],
                "fragment_index": block["table_fragment_index"],
                "source_row_index": source_row,
            }
        ]
        for source_row in block["logical_row_source_indexes"]
    ]
    return payload


def _normalized_label(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _row_label_overlap(left: dict, right: dict) -> float:
    left_labels = {
        _normalized_label(row[0])
        for row in left["logical_rows"]
        if row and row[0]
    }
    right_labels = {
        _normalized_label(row[0])
        for row in right["logical_rows"]
        if row and row[0]
    }
    return len(left_labels & right_labels) / min(len(left_labels), len(right_labels)) if left_labels and right_labels else 0.0


def _full_header_paths(block: dict) -> list[list[str]]:
    context = block.get("logical_header_context") or []
    return [
        ([*context, *path] if index else path)
        for index, path in enumerate(block["logical_header_paths"])
    ]


def _horizontal_is_safe(blocks: list[dict]) -> bool:
    if len(blocks) < 2 or any(block["logical_width"] < 2 for block in blocks):
        return False
    for block in blocks:
        labels = [_normalized_label(row[0]) for row in block["logical_rows"] if row and row[0]]
        if len(labels) != len(set(labels)):
            return False
    if any(
        _row_label_overlap(left, right) < 0.80
        for left, right in zip(blocks, blocks[1:])
    ):
        return False
    path_sets = []
    for block in blocks:
        paths = {
            tuple(normalize.casefold() for normalize in path)
            for path in _full_header_paths(block)[1:]
            if path
        }
        if not paths:
            return False
        path_sets.append(paths)
    return all(not (left & right) for index, left in enumerate(path_sets) for right in path_sets[index + 1 :])


def _vertical_is_safe(blocks: list[dict]) -> bool:
    first = blocks[0]
    return len(blocks) > 1 and all(
        block["logical_width"] == first["logical_width"]
        and block["logical_header_paths"] == first["logical_header_paths"]
        and (block.get("logical_header_context") or [])
        == (first.get("logical_header_context") or [])
        and block["logical_column_units"] == first["logical_column_units"]
        for block in blocks[1:]
    )


def _remap_rowspans(block: dict, row_offset: int = 0, column_offset: int = 0) -> list[dict]:
    return [
        {
            **value,
            "source_block_id": block["block_id"],
            "html_table_id": block["html_table_id"],
            "fragment_index": block["table_fragment_index"],
            "logical_column": value["logical_column"] + column_offset,
            "logical_row_start": value["logical_row_start"] + row_offset,
            "logical_row_end": value["logical_row_end"] + row_offset,
        }
        for value in block.get("logical_body_rowspans") or []
    ]


def _single_or_vertical(blocks: list[dict], mode: str) -> dict:
    first = blocks[0]
    rows = []
    roles = []
    states = []
    sources = []
    row_sources = []
    rowspans = []
    for block in blocks:
        row_offset = len(rows)
        payload = _fragment_payload(block)
        rows.extend(copy.deepcopy(block["logical_rows"]))
        roles.extend(block["logical_row_roles"])
        states.extend(copy.deepcopy(block["logical_cell_states"]))
        sources.extend(payload["logical_cell_sources"])
        row_sources.extend(payload["logical_row_sources"])
        rowspans.extend(_remap_rowspans(block, row_offset=row_offset))
    return {
        "composition_mode": mode,
        "logical_width": first["logical_width"],
        "logical_header_rows": copy.deepcopy(first["logical_header_rows"]),
        "logical_header_paths": copy.deepcopy(first["logical_header_paths"]),
        "logical_header_context": copy.deepcopy(first.get("logical_header_context") or []),
        "logical_header_context_source_raw_cell_ids": copy.deepcopy(first.get("logical_header_context_source_raw_cell_ids") or []),
        "logical_column_headers": copy.deepcopy(first["logical_column_headers"]),
        "logical_column_header_metadata": copy.deepcopy(first["logical_column_header_metadata"]),
        "logical_column_units": copy.deepcopy(first["logical_column_units"]),
        "logical_column_unit_metadata": copy.deepcopy(first["logical_column_unit_metadata"]),
        "logical_columns": copy.deepcopy(first["logical_columns"]),
        "logical_rows": rows,
        "logical_row_roles": roles,
        "logical_cell_states": states,
        "logical_cell_sources": sources,
        "logical_body_rowspans": rowspans,
        "logical_row_sources": row_sources,
    }


def _horizontal(blocks: list[dict]) -> dict:
    first = blocks[0]
    labels_in_order = []
    label_display = {}
    row_lookup = []
    for block in blocks:
        lookup = {}
        for row_index, row in enumerate(block["logical_rows"]):
            key = _normalized_label(row[0])
            lookup[key] = row_index
            if key not in label_display:
                label_display[key] = row[0]
                labels_in_order.append(key)
        row_lookup.append(lookup)

    header_paths = [copy.deepcopy(first["logical_header_paths"][0])]
    headers = [first["logical_column_headers"][0] or "Line item"]
    header_metadata = [copy.deepcopy(first["logical_column_header_metadata"][0])]
    units = [first["logical_column_units"][0]]
    unit_metadata = [copy.deepcopy(first["logical_column_unit_metadata"][0])]
    columns = [copy.deepcopy(first["logical_columns"][0])]
    column_maps = []
    next_column = 1
    for block in blocks:
        mapping = {0: 0}
        full_paths = _full_header_paths(block)
        for source_column in range(1, block["logical_width"]):
            mapping[source_column] = next_column
            path = full_paths[source_column]
            header_paths.append(copy.deepcopy(path))
            headers.append(" — ".join(path) or block["logical_column_headers"][source_column])
            header_metadata.append(copy.deepcopy(block["logical_column_header_metadata"][source_column]))
            units.append(block["logical_column_units"][source_column])
            unit_metadata.append(copy.deepcopy(block["logical_column_unit_metadata"][source_column]))
            column = copy.deepcopy(block["logical_columns"][source_column])
            column["logical_index"] = next_column
            columns.append(column)
            next_column += 1
        column_maps.append(mapping)

    width = next_column
    rows = []
    roles = []
    states = []
    sources = []
    row_sources = []
    rowspans = []
    fragment_payloads = [_fragment_payload(block) for block in blocks]
    for output_row, key in enumerate(labels_in_order):
        row = [""] * width
        state_row = ["missing_blank_in_aligned_lane"] * width
        source_row = [[] for _ in range(width)]
        contributions = []
        role = "data"
        row[0] = label_display[key]
        state_row[0] = "present"
        for fragment_index, block in enumerate(blocks):
            if key not in row_lookup[fragment_index]:
                continue
            source_row_index = row_lookup[fragment_index][key]
            role = block["logical_row_roles"][source_row_index]
            contributions.extend(fragment_payloads[fragment_index]["logical_row_sources"][source_row_index])
            for source_column, target_column in column_maps[fragment_index].items():
                value = block["logical_rows"][source_row_index][source_column]
                if source_column == 0 and source_row[0]:
                    continue
                row[target_column] = value
                state_row[target_column] = block["logical_cell_states"][source_row_index][source_column]
                source_row[target_column].extend(fragment_payloads[fragment_index]["logical_cell_sources"][source_row_index][source_column])
        rows.append(row)
        roles.append(role)
        states.append(state_row)
        sources.append(source_row)
        row_sources.append(contributions)
    for fragment_index, block in enumerate(blocks):
        for value in block.get("logical_body_rowspans") or []:
            source_start = value["logical_row_start"]
            key = _normalized_label(block["logical_rows"][source_start][0])
            if key not in labels_in_order:
                continue
            target_row = labels_in_order.index(key)
            rowspans.append(
                {
                    **value,
                    "source_block_id": block["block_id"],
                    "html_table_id": block["html_table_id"],
                    "fragment_index": block["table_fragment_index"],
                    "logical_column": column_maps[fragment_index][value["logical_column"]],
                    "logical_row_start": target_row,
                    "logical_row_end": target_row + (value["logical_row_end"] - value["logical_row_start"]),
                }
            )
    return {
        "composition_mode": "horizontal",
        "logical_width": width,
        "logical_header_rows": [],
        "logical_header_paths": header_paths,
        "logical_header_context": [],
        "logical_header_context_source_raw_cell_ids": [],
        "logical_column_headers": headers,
        "logical_column_header_metadata": header_metadata,
        "logical_column_units": units,
        "logical_column_unit_metadata": unit_metadata,
        "logical_columns": columns,
        "logical_rows": rows,
        "logical_row_roles": roles,
        "logical_cell_states": states,
        "logical_cell_sources": sources,
        "logical_body_rowspans": rowspans,
        "logical_row_sources": row_sources,
    }


def _compound_text(blocks: list[dict]) -> str:
    lines = []
    title = blocks[0].get("title")
    if title:
        lines.extend([title, ""])
    for index, block in enumerate(blocks, start=1):
        fragment = copy.deepcopy(block)
        fragment["title"] = None
        lines.extend([f"Fragment {index}", "", render_logical_table(fragment)])
        if index != len(blocks):
            lines.append("")
    return "\n".join(lines)


def compose_logical_table(blocks: list[dict]) -> dict:
    for block in blocks:
        validate_current_table_block(block)
    if len(blocks) == 1:
        composition = _single_or_vertical(blocks, "single")
    elif _vertical_is_safe(blocks):
        composition = _single_or_vertical(blocks, "vertical")
    elif _horizontal_is_safe(blocks):
        composition = _horizontal(blocks)
    else:
        composition = {
            "composition_mode": "compound",
            "logical_width": None,
            "logical_header_rows": [],
            "logical_header_paths": [],
            "logical_header_context": [],
            "logical_header_context_source_raw_cell_ids": [],
            "logical_column_headers": [],
            "logical_column_header_metadata": [],
            "logical_column_units": [],
            "logical_column_unit_metadata": [],
            "logical_columns": [],
            "logical_rows": [],
            "logical_row_roles": [],
            "logical_cell_states": [],
            "logical_cell_sources": [],
            "logical_body_rowspans": [],
            "logical_row_sources": [],
        }
    composition["logical_fragments"] = [_fragment_payload(block) for block in blocks]
    if composition["composition_mode"] == "compound":
        composition["rendered_text"] = _compound_text(blocks)
    else:
        renderable = {
            **composition,
            "title": blocks[0].get("title"),
            "units": blocks[0].get("units"),
            "header_mode": blocks[0].get("header_mode"),
        }
        composition["rendered_text"] = render_logical_table(renderable)
    return composition


def chunk_table(blocks: list[dict] | dict, config: dict) -> list[dict]:
    if isinstance(blocks, dict):
        blocks = [blocks]
    if not blocks:
        return []
    logical_id = blocks[0]["logical_table_id"]
    if any(block["logical_table_id"] != logical_id for block in blocks):
        raise ValueError("Cannot compose unrelated logical table IDs")
    composition = compose_logical_table(blocks)
    first = blocks[0]
    effective_prefix = "\n".join(
        first.get("effective_section_path")
        or first.get("section_path")
        or [first["section"]]
    )
    text = f"{effective_prefix}\n\n{composition.pop('rendered_text')}"
    return [
        {
            **source_metadata(blocks),
            "content_type": "table",
            "table_schema_version": TABLE_SCHEMA_VERSION,
            "table_heuristics_version": TABLE_HEURISTICS_VERSION,
            "table_class": first["table_class"],
            "table_kind": first["table_kind"],
            "title": first.get("title"),
            "title_source": first.get("title_source"),
            "title_source_block_id": first.get("title_source_block_id"),
            "title_source_raw_cell_ids": copy.deepcopy(
                first.get("title_source_raw_cell_ids") or []
            ),
            "units": first.get("units"),
            "header_mode": first.get("header_mode"),
            "logical_table_id": logical_id,
            "table_fragment_count": len(blocks),
            "fragment_block_ids": [block["block_id"] for block in blocks],
            "html_table_ids": [block["html_table_id"] for block in blocks],
            "source_group": f"table-{logical_id}",
            **composition,
            # Transitional aliases are logical and never physical.
            "table_headers": copy.deepcopy(composition["logical_header_rows"]),
            "table_rows": copy.deepcopy(composition["logical_rows"]),
            "column_units": copy.deepcopy(composition["logical_column_units"]),
            "text": text,
        }
    ]


def chunk_blocks(
    blocks: list[dict],
    config: dict,
    *,
    source_processed_sha256: str | None = None,
    chunking_config_sha256: str | None = None,
) -> list[dict]:
    validate_config(config)
    tokenizer = get_tokenizer(config)
    narrative_types = set(config["narrative_content_types"])
    table_types = set(config["table_content_types"])
    excluded_types = set(config["excluded_content_types"])
    chunks = []
    narrative_group = []
    narrative_key = None
    table_group = []
    closed_table_ids = set()
    group_number = 0

    def flush_narrative() -> None:
        nonlocal narrative_group, narrative_key, group_number
        if narrative_group:
            group_number += 1
            chunks.extend(
                chunk_narrative(
                    narrative_group,
                    config,
                    f"narrative-{group_number:04d}",
                    tokenizer,
                )
            )
        narrative_group = []
        narrative_key = None

    def flush_table() -> None:
        nonlocal table_group
        if table_group:
            logical_id = table_group[0]["logical_table_id"]
            chunks.extend(chunk_table(table_group, config))
            closed_table_ids.add(logical_id)
        table_group = []

    for block in blocks:
        content_type = block["content_type"]
        if content_type in excluded_types:
            flush_narrative()
            flush_table()
        elif content_type in table_types:
            flush_narrative()
            validate_current_table_block(block)
            logical_id = block["logical_table_id"]
            if table_group and table_group[0]["logical_table_id"] != logical_id:
                flush_table()
            if logical_id in closed_table_ids:
                raise ValueError(f"Logical table {logical_id} reappears nonlocally")
            table_group.append(block)
        elif content_type in narrative_types:
            flush_table()
            key = (block["section"], tuple(block.get("section_path") or []))
            if narrative_key is not None and key != narrative_key:
                flush_narrative()
            narrative_key = key
            narrative_group.append(block)
        else:
            raise ValueError(f"Unsupported content type {content_type!r}")
    flush_narrative()
    flush_table()

    source_hash = source_processed_sha256 or blocks_sha256(blocks)
    config_hash = chunking_config_sha256 or config_sha256(config)
    ticker = blocks[0]["ticker"]
    year = blocks[0]["filing_year"]
    for index, chunk in enumerate(chunks, start=1):
        chunk["chunk_schema_version"] = CHUNK_SCHEMA_VERSION
        chunk["chunking_config_sha256"] = config_hash
        chunk["source_processed_sha256"] = source_hash
        chunk["chunk_id"] = f"{ticker}-{year}-CHUNK-{index:06d}"
        chunk["chunk_index"] = index
    validate_chunks(chunks, config, tokenizer)
    return chunks


def _markdown_subtables_valid(text: str) -> bool:
    groups = []
    current = []
    for line in text.splitlines():
        if line.startswith("|"):
            current.append(line)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return bool(groups) and all(validate_markdown("\n".join(group)) for group in groups)


def validate_chunks(chunks: list[dict], config: dict, tokenizer=None) -> None:
    if not chunks:
        raise ValueError("Chunking produced no output")
    tokenizer = tokenizer or get_tokenizer(config)
    logical_ids = set()
    fragment_ids = set()
    expected_source_hash = chunks[0].get("source_processed_sha256")
    expected_config_hash = chunks[0].get("chunking_config_sha256")
    for index, chunk in enumerate(chunks, start=1):
        if chunk.get("chunk_schema_version") != CHUNK_SCHEMA_VERSION:
            raise ValueError(f"Stale chunk schema: {chunk.get('chunk_id')}")
        if chunk.get("source_processed_sha256") != expected_source_hash or chunk.get("chunking_config_sha256") != expected_config_hash:
            raise ValueError("Chunks contain mixed release hashes")
        if chunk["chunk_index"] != index or not chunk["block_ids"]:
            raise ValueError(f"Invalid chunk provenance: {chunk['chunk_id']}")
        if not chunk["text"].strip():
            raise ValueError(f"Invalid chunk length: {chunk['chunk_id']}")
        if chunk["content_type"] != "table" and count_tokens(chunk["text"], tokenizer) > config["chunk_size"]:
            raise ValueError(f"Invalid chunk length: {chunk['chunk_id']}")
        if chunk["content_type"] != "table":
            continue
        if chunk.get("table_schema_version") != TABLE_SCHEMA_VERSION or chunk.get("table_heuristics_version") != TABLE_HEURISTICS_VERSION:
            raise ValueError(f"Stale table chunk: {chunk['chunk_id']}")
        logical_id = chunk.get("logical_table_id")
        if not logical_id or logical_id in logical_ids:
            raise ValueError(f"Duplicate logical-table chunk: {logical_id}")
        logical_ids.add(logical_id)
        ids = chunk.get("fragment_block_ids") or []
        if len(ids) != chunk.get("table_fragment_count") or len(ids) != len(set(ids)):
            raise ValueError(f"Invalid fragment provenance: {chunk['chunk_id']}")
        if any(value in fragment_ids for value in ids):
            raise ValueError(f"A fragment appears in multiple chunks: {chunk['chunk_id']}")
        fragment_ids.update(ids)
        if set(ids) != set(chunk["block_ids"]):
            raise ValueError(f"Chunk block IDs do not match fragments: {chunk['chunk_id']}")
        if not _markdown_subtables_valid(chunk["text"]):
            raise ValueError(f"Invalid logical Markdown: {chunk['chunk_id']}")
        fragment_lookup = {}
        for fragment in chunk.get("logical_fragments") or []:
            fragment_block_id = fragment.get("block_id")
            if fragment.get("table_schema_version") != TABLE_SCHEMA_VERSION:
                raise ValueError(f"Stale logical fragment: {chunk['chunk_id']}")
            if fragment.get("table_heuristics_version") != TABLE_HEURISTICS_VERSION:
                raise ValueError(f"Unknown fragment heuristics: {chunk['chunk_id']}")
            if fragment.get("logical_table_id") != logical_id:
                raise ValueError(f"Fragment logical ID mismatch: {chunk['chunk_id']}")
            if fragment_block_id not in ids or fragment_block_id in fragment_lookup:
                raise ValueError(f"Invalid logical fragment identity: {chunk['chunk_id']}")
            fragment_lookup[fragment_block_id] = fragment
            fragment_width = fragment.get("logical_width")
            fragment_rows = fragment.get("logical_rows") or []
            if not fragment_width or any(len(row) != fragment_width for row in fragment_rows):
                raise ValueError(f"Invalid fragment logical width: {chunk['chunk_id']}")
            for field in (
                "logical_header_paths",
                "logical_column_headers",
                "logical_column_units",
                "logical_columns",
            ):
                if len(fragment.get(field) or []) != fragment_width:
                    raise ValueError(f"Invalid fragment {field}: {chunk['chunk_id']}")
            for field in ("logical_cell_states", "logical_cell_sources"):
                matrix = fragment.get(field) or []
                if len(matrix) != len(fragment_rows) or any(
                    len(row) != fragment_width for row in matrix
                ):
                    raise ValueError(f"Invalid fragment {field}: {chunk['chunk_id']}")
            raw_ids = set(fragment.get("source_raw_cell_ids") or [])
            if not raw_ids:
                raise ValueError(f"Fragment lacks raw-cell inventory: {chunk['chunk_id']}")
            for row in fragment.get("logical_cell_sources") or []:
                for entries in row:
                    for entry in entries:
                        if (
                            entry.get("source_block_id") != fragment_block_id
                            or entry.get("html_table_id") != fragment.get("html_table_id")
                            or entry.get("fragment_index") != fragment.get("table_fragment_index")
                            or not set(entry.get("raw_cell_ids") or []).issubset(raw_ids)
                        ):
                            raise ValueError(
                                f"Invalid fragment cell provenance: {chunk['chunk_id']}"
                            )
        if set(fragment_lookup) != set(ids):
            raise ValueError(f"Chunk fragment payloads are incomplete: {chunk['chunk_id']}")
        if chunk["composition_mode"] != "compound":
            width = chunk["logical_width"]
            if width <= 0 or any(len(row) != width for row in chunk["logical_rows"]):
                raise ValueError(f"Invalid composed logical width: {chunk['chunk_id']}")
            for field in (
                "logical_header_paths",
                "logical_column_headers",
                "logical_column_units",
                "logical_columns",
            ):
                if len(chunk.get(field) or []) != width:
                    raise ValueError(f"Invalid composed {field}: {chunk['chunk_id']}")
            for field in ("logical_cell_states", "logical_cell_sources"):
                matrix = chunk[field]
                if len(matrix) != len(chunk["logical_rows"]) or any(len(row) != width for row in matrix):
                    raise ValueError(f"Invalid {field}: {chunk['chunk_id']}")
            for row, states, sources in zip(chunk["logical_rows"], chunk["logical_cell_states"], chunk["logical_cell_sources"]):
                for value, state, source in zip(row, states, sources):
                    if value and not source:
                        raise ValueError(f"Logical cell lacks provenance: {chunk['chunk_id']}")
                    if not value and state == "present":
                        raise ValueError(f"Blank logical cell marked present: {chunk['chunk_id']}")
                    for entry in source:
                        fragment = fragment_lookup.get(entry.get("source_block_id"))
                        if fragment is None:
                            raise ValueError(f"Unknown composed source block: {chunk['chunk_id']}")
                        if (
                            entry.get("html_table_id") != fragment.get("html_table_id")
                            or entry.get("fragment_index") != fragment.get("table_fragment_index")
                            or not set(entry.get("raw_cell_ids") or []).issubset(
                                set(fragment.get("source_raw_cell_ids") or [])
                            )
                        ):
                            raise ValueError(f"Invalid composed cell provenance: {chunk['chunk_id']}")


def actual_overlaps(chunks: list[dict]) -> list[int]:
    groups = defaultdict(list)
    for chunk in chunks:
        if chunk["content_type"] == "narrative":
            groups[chunk["source_group"]].append(chunk)
    overlaps = []
    for group in groups.values():
        for previous, current in zip(group, group[1:]):
            overlaps.append(
                max(0, previous["source_token_end"] - current["source_token_start"])
            )
    return overlaps


def chunk_statistics(chunks: list[dict], config: dict, blocks: list[dict] | None = None) -> dict:
    tokenizer = get_tokenizer(config)
    lengths = sorted(count_tokens(chunk["text"], tokenizer) for chunk in chunks)
    types = Counter(chunk["content_type"] for chunk in chunks)
    overlaps = actual_overlaps(chunks)
    narrative = [chunk for chunk in chunks if chunk["content_type"] == "narrative"]
    boundary_pattern = re.compile(r"[.!?;:][\"')\]]*$")
    boundary_hits = sum(bool(boundary_pattern.search(chunk["text"].rstrip())) for chunk in narrative)
    relevant_blocks = []
    relevant_anchors = set()
    block_lookup = {}
    if blocks:
        block_lookup = {block["block_id"]: block for block in blocks}
        relevant_blocks = [
            block["block_id"]
            for block in blocks
            if block["content_type"] not in {"heading", "navigation"}
        ]
        relevant_anchors = {
            block.get("source_anchor")
            for block in blocks
            if block["content_type"] not in {"heading", "navigation"}
            and block.get("source_anchor")
        }
    covered = {block_id for chunk in chunks for block_id in chunk["block_ids"]}
    covered_anchors = {
        anchor
        for chunk in chunks
        for anchor in chunk.get("source_anchors") or []
        if anchor
    }
    section_hits = 0
    table_context_hits = 0
    table_chunks = [chunk for chunk in chunks if chunk["content_type"] == "table"]
    for chunk in chunks:
        source_sections = {
            block_lookup[block_id]["section"]
            for block_id in chunk["block_ids"]
            if block_id in block_lookup
        }
        section_hits += len(source_sections) <= 1
    for chunk in table_chunks:
        expected_context = [chunk.get("title")]
        if chunk.get("units"):
            expected_context.append(f"Units: {chunk['units']}")
        if chunk.get("logical_header_context"):
            expected_context.append(
                "Header context: "
                + " — ".join(chunk["logical_header_context"])
            )
        expected_context.extend(chunk.get("logical_column_headers") or [])
        table_context_hits += all(
            not value or value in chunk["text"] for value in expected_context
        )
    markdown_valid = sum(
        _markdown_subtables_valid(chunk["text"]) for chunk in table_chunks
    )
    marker_values = {"$", "€", "£", "¥", "%"}
    standalone_markers = sum(
        value.strip() in marker_values
        for chunk in table_chunks
        for fragment in chunk.get("logical_fragments") or []
        for row in fragment.get("logical_rows") or []
        for value in row
    )
    fallback_fragments = sum(
        bool((fragment.get("normalization_diagnostics") or {}).get("fallback_used"))
        for chunk in table_chunks
        for fragment in chunk.get("logical_fragments") or []
    )
    table_lengths = sorted(count_tokens(chunk["text"], tokenizer) for chunk in table_chunks)
    fragment_payloads = [
        fragment
        for chunk in table_chunks
        for fragment in chunk.get("logical_fragments") or []
    ]
    logical_densities = []
    for chunk in table_chunks:
        fragments = (
            chunk.get("logical_fragments") or []
            if chunk.get("composition_mode") == "compound"
            else [chunk]
        )
        empty = 0
        slots = 0
        for fragment in fragments:
            width = int(fragment.get("logical_width") or 0)
            for row, role in zip(
                fragment.get("logical_rows") or [],
                fragment.get("logical_row_roles") or [],
            ):
                if role in {"section_label", "footnote"}:
                    continue
                empty += sum(not value for value in row)
                slots += width
        logical_densities.append(empty / slots if slots else 0.0)
    return {
        "chunk_schema_version": CHUNK_SCHEMA_VERSION,
        "table_schema_version": TABLE_SCHEMA_VERSION,
        "table_heuristics_version": TABLE_HEURISTICS_VERSION,
        "chunking_config_sha256": chunks[0]["chunking_config_sha256"],
        "source_processed_sha256": chunks[0]["source_processed_sha256"],
        "strategy": config.get("strategy", "recursive"),
        "length_function": "tokens",
        "tokenizer_model": config["tokenizer_model"],
        "chunk_count": len(chunks),
        "chunk_size": config["chunk_size"],
        "configured_overlap": config["chunk_overlap"],
        "actual_overlap_median": statistics.median(overlaps) if overlaps else 0,
        "length_min": lengths[0],
        "length_median": statistics.median(lengths),
        "length_p95": lengths[math.ceil(len(lengths) * 0.95) - 1],
        "length_max": lengths[-1],
        "boundary_accuracy": boundary_hits / len(narrative) if narrative else 0,
        "section_accuracy": section_hits / len(chunks),
        "table_context_copy_completeness": (
            table_context_hits / len(table_chunks) if table_chunks else 1
        ),
        "logical_table_count": len(
            {chunk["logical_table_id"] for chunk in table_chunks}
        ),
        "table_fragment_count": sum(
            chunk["table_fragment_count"] for chunk in table_chunks
        ),
        "table_kind_counts": dict(
            sorted(Counter(chunk.get("table_kind") for chunk in table_chunks).items())
        ),
        "table_class_counts": dict(
            sorted(Counter(chunk.get("table_class") for chunk in table_chunks).items())
        ),
        "header_mode_counts": dict(
            sorted(Counter(chunk.get("header_mode") for chunk in table_chunks).items())
        ),
        "title_source_counts": dict(
            sorted(Counter(chunk.get("title_source") for chunk in table_chunks).items())
        ),
        "composition_mode_counts": dict(
            sorted(
                Counter(chunk.get("composition_mode") for chunk in table_chunks).items()
            )
        ),
        "accepted_continuation_link_count": sum(
            fragment.get("is_continuation") for fragment in fragment_payloads
        ),
        "logical_empty_density_median": (
            statistics.median(logical_densities) if logical_densities else 0
        ),
        "logical_empty_density_p95": (
            sorted(logical_densities)[math.ceil(len(logical_densities) * 0.95) - 1]
            if logical_densities
            else 0
        ),
        "logical_empty_density_max": max(logical_densities, default=0),
        "logical_tables_over_50_percent_empty": sum(
            value > 0.5 for value in logical_densities
        ),
        "table_length_max": max(table_lengths, default=0),
        "table_inputs_over_narrative_limit": sum(
            value > config["chunk_size"] for value in table_lengths
        ),
        "table_markdown_validity": (
            markdown_valid / len(table_chunks) if table_chunks else 1
        ),
        "standalone_marker_count": standalone_markers,
        "normalization_fallback_fragment_count": fallback_fragments,
        "source_block_coverage": (
            sum(block_id in covered for block_id in relevant_blocks) / len(relevant_blocks)
            if relevant_blocks
            else 1
        ),
        "source_anchor_coverage": (
            len(relevant_anchors & covered_anchors) / len(relevant_anchors)
            if relevant_anchors
            else 1
        ),
        "source_coverage_scope": "non-heading non-navigation processed blocks",
        "narrative_chunks": types.get("narrative", 0),
        "table_chunks": types.get("table", 0),
    }


def write_jsonl(records: list[dict], path: Path, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Chunk output already exists: {path}")
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary_path = Path(file.name)
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def write_json(value: dict, path: Path, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Chunk statistics already exist: {path}")
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as output_file:
            temporary_path = Path(output_file.name)
            json.dump(value, output_file, indent=2)
            output_file.write("\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunk structured filing blocks.")
    parser.add_argument("company", choices=sorted(COMPANIES))
    parser.add_argument(
        "--processed-directory",
        type=Path,
        default=DEFAULT_PROCESSED_DIRECTORY,
        help="Directory containing processed filing blocks grouped by ticker.",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()

    input_path = find_latest_processed_filing(
        arguments.company, arguments.processed_directory
    )
    config = load_chunk_config(arguments.config)
    blocks = load_jsonl(input_path)
    chunks = chunk_blocks(
        blocks,
        config,
        source_processed_sha256=sha256_file(input_path),
        chunking_config_sha256=sha256_file(arguments.config),
    )
    output = arguments.output or (
        PROJECT_ROOT
        / "data"
        / "chunks"
        / blocks[0]["ticker"]
        / input_path.name.replace(".blocks.jsonl", ".chunks.jsonl")
    )
    stats_output = output.with_suffix(".stats.json")
    if not arguments.overwrite:
        existing = [path for path in (output, stats_output) if path.exists()]
        if existing:
            raise FileExistsError(
                "Chunk release output already exists: "
                + ", ".join(str(path) for path in existing)
            )
    write_jsonl(chunks, output, arguments.overwrite)
    stats = chunk_statistics(chunks, config, blocks)
    write_json(stats, stats_output, arguments.overwrite)
    print(f"Wrote {len(chunks)} chunks to {output}")


if __name__ == "__main__":
    main()
