import argparse
import json
import os
import tempfile
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
    "content_type",
    "text",
    "source_tag",
    "source_anchor",
    "page_start",
    "page_end",
    "source_url",
)


def validate_blocks(blocks: list[dict]) -> None:
    """Validate required fields and deterministic ordering before serialization."""
    if not blocks:
        raise ValueError("Cannot serialize an empty block collection")

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

        try:
            json.dumps(block, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Block {expected_index} is not JSON serializable") from exc


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
    return write_blocks_jsonl(blocks, output_path, overwrite=overwrite)


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
