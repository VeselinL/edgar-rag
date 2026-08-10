import argparse
import json
import math
import os
import re
import statistics
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "data" / "chunks" / "chunking-config.json"
DEFAULT_INPUT_PATH = (
    PROJECT_ROOT / "data" / "processed" / "MBLY" / "2025-10-K.blocks.jsonl"
)

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
    "source_url",
)


def load_jsonl(path: str | Path) -> list[dict]:
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


def load_chunk_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    with Path(path).open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    validate_config(config)
    return config


def validate_config(config: dict) -> None:
    size = int(config["chunk_size"])
    overlap = int(config["chunk_overlap"])
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")
    if config.get("strategy", "recursive") not in {"recursive", "fixed"}:
        raise ValueError("strategy must be 'recursive' or 'fixed'")


def recursive_spans(text: str, size: int, overlap: int, separators: list[str]):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=separators,
        length_function=len,
        add_start_index=True,
        strip_whitespace=True,
    )
    for document in splitter.create_documents([text]):
        start = document.metadata["start_index"]
        yield document.page_content, start, start + len(document.page_content)


def fixed_spans(text: str, size: int, overlap: int):
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        raw_chunk = text[start:end]
        content = raw_chunk.strip()
        if content:
            content_start = start + len(raw_chunk) - len(raw_chunk.lstrip())
            yield content, content_start, content_start + len(content)
        if end == len(text):
            break
        start = end - overlap


def split_spans(text: str, size: int, config: dict):
    if size <= 0:
        raise ValueError("Chunk context exceeds the configured chunk size")
    overlap = min(int(config["chunk_overlap"]), size - 1)
    if config.get("strategy", "recursive") == "fixed":
        return list(fixed_spans(text, size, overlap))
    return list(recursive_spans(text, size, overlap, config["separators"]))


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


def chunk_narrative(blocks: list[dict], config: dict, group_id: str) -> list[dict]:
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
    spans = split_spans(body, config["chunk_size"] - len(prefix) - 2, config)
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
                "text": f"{prefix}\n\n{text}",
            }
        )
    return chunks


def render_row(row: list) -> str:
    return " | ".join("" if value is None else str(value).strip() for value in row).rstrip()


def chunk_table(block: dict, config: dict) -> list[dict]:
    rows = block.get("rows") or []
    header_indexes = set(block.get("header_row_indexes") or [])
    headers = [row for index, row in enumerate(rows) if index in header_indexes]
    body_rows = [(index, row) for index, row in enumerate(rows) if index not in header_indexes]
    if not body_rows:
        return []

    context = [section_prefix(block)]
    if block.get("title") and block["title"] not in (block.get("section_path") or []):
        context.append(block["title"])
    if block.get("units"):
        context.append(f"Units: {block['units']}")
    if any(block.get("column_units") or []):
        context.append(f"Column units: {render_row(block['column_units'])}")
    context.extend(render_row(row) for row in headers)
    prefix = "\n".join(context)
    table_text = "\n".join(render_row(row) for _, row in body_rows)
    return [
        {
            **source_metadata([block]),
            "content_type": "table",
            "table_class": block.get("table_class", "unknown"),
            "title": block.get("title"),
            "units": block.get("units"),
            "column_units": block.get("column_units") or [],
            "table_headers": headers,
            "table_rows": [row for _, row in body_rows],
            "table_row_indexes": [index for index, _ in body_rows],
            "source_group": f"table-{block['block_id']}",
            "text": f"{prefix}\n{table_text}",
        }
    ]


def chunk_blocks(blocks: list[dict], config: dict) -> list[dict]:
    validate_config(config)
    narrative_types = set(config["narrative_content_types"])
    table_types = set(config["table_content_types"])
    excluded_types = set(config["excluded_content_types"])
    chunks = []
    narrative_group = []
    narrative_key = None
    group_number = 0

    def flush() -> None:
        nonlocal narrative_group, narrative_key, group_number
        if narrative_group:
            group_number += 1
            chunks.extend(
                chunk_narrative(narrative_group, config, f"narrative-{group_number:04d}")
            )
        narrative_group = []
        narrative_key = None

    for block in blocks:
        content_type = block["content_type"]
        if content_type in excluded_types:
            flush()
        elif content_type in table_types:
            flush()
            chunks.extend(chunk_table(block, config))
        elif content_type in narrative_types:
            key = (block["section"], tuple(block.get("section_path") or []))
            if narrative_key is not None and key != narrative_key:
                flush()
            narrative_key = key
            narrative_group.append(block)
        else:
            raise ValueError(f"Unsupported content type {content_type!r}")
    flush()

    ticker = blocks[0]["ticker"]
    year = blocks[0]["filing_year"]
    for index, chunk in enumerate(chunks, start=1):
        chunk["chunk_id"] = f"{ticker}-{year}-CHUNK-{index:06d}"
        chunk["chunk_index"] = index
    validate_chunks(chunks, config)
    return chunks


def validate_chunks(chunks: list[dict], config: dict) -> None:
    if not chunks:
        raise ValueError("Chunking produced no output")
    for index, chunk in enumerate(chunks, start=1):
        if chunk["chunk_index"] != index or not chunk["block_ids"]:
            raise ValueError(f"Invalid chunk provenance: {chunk['chunk_id']}")
        if not chunk["text"].strip():
            raise ValueError(f"Invalid chunk length: {chunk['chunk_id']}")
        if (
            chunk["content_type"] != "table"
            and len(chunk["text"]) > config["chunk_size"]
        ):
            raise ValueError(f"Invalid chunk length: {chunk['chunk_id']}")


def actual_overlaps(chunks: list[dict]) -> list[int]:
    groups = defaultdict(list)
    for chunk in chunks:
        if chunk["content_type"] == "narrative":
            groups[chunk["source_group"]].append(chunk)
    overlaps = []
    for group in groups.values():
        for previous, current in zip(group, group[1:]):
            overlaps.append(
                max(0, previous["source_text_end"] - current["source_text_start"])
            )
    return overlaps


def chunk_statistics(chunks: list[dict], config: dict, blocks: list[dict] | None = None) -> dict:
    lengths = sorted(len(chunk["text"]) for chunk in chunks)
    types = Counter(chunk["content_type"] for chunk in chunks)
    overlaps = actual_overlaps(chunks)
    narrative = [chunk for chunk in chunks if chunk["content_type"] == "narrative"]
    boundary_pattern = re.compile(r"[.!?;:][\"')\]]*$")
    boundary_hits = sum(bool(boundary_pattern.search(chunk["text"].rstrip())) for chunk in narrative)
    relevant_blocks = []
    block_lookup = {}
    if blocks:
        block_lookup = {block["block_id"]: block for block in blocks}
        relevant_blocks = [
            block["block_id"]
            for block in blocks
            if block["content_type"] not in {"heading", "navigation"}
        ]
    covered = {block_id for chunk in chunks for block_id in chunk["block_ids"]}
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
        if any(chunk.get("column_units") or []):
            expected_context.append(
                f"Column units: {render_row(chunk['column_units'])}"
            )
        expected_context.extend(render_row(row) for row in chunk.get("table_headers", []))
        table_context_hits += all(
            not value or value in chunk["text"] for value in expected_context
        )
    return {
        "strategy": config.get("strategy", "recursive"),
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
        "table_context_accuracy": (
            table_context_hits / len(table_chunks) if table_chunks else 1
        ),
        "source_block_coverage": (
            sum(block_id in covered for block_id in relevant_blocks) / len(relevant_blocks)
            if relevant_blocks
            else 1
        ),
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunk structured filing blocks.")
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()

    config = load_chunk_config(arguments.config)
    blocks = load_jsonl(arguments.input)
    chunks = chunk_blocks(blocks, config)
    output = arguments.output or (
        PROJECT_ROOT
        / "data"
        / "chunks"
        / blocks[0]["ticker"]
        / arguments.input.name.replace(".blocks.jsonl", ".chunks.jsonl")
    )
    write_jsonl(chunks, output, arguments.overwrite)
    stats = chunk_statistics(chunks, config, blocks)
    output.with_suffix(".stats.json").write_text(
        json.dumps(stats, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(chunks)} chunks to {output}")


if __name__ == "__main__":
    main()
