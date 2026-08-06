import argparse
import html as html_lib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
import unicodedata
from datetime import datetime
from pathlib import Path

from fetch_data import COMPANIES

from lxml import html as lxml_html

FILING_FILENAME_PATTERN = re.compile(r"(?P<year>\d{4})-10-K\.html$")
ITEM_HEADING_PATTERN = re.compile(
    r"^item\s+(?P<number>\d{1,2}[a-z]?)\s*[.\-—:]?\s*(?P<title>.+?)\.?$",
    re.IGNORECASE,
)
PAGE_NUMBER_PATTERN = re.compile(r"^(?:\d{1,3}|[ivxlcdm]{1,8})$", re.IGNORECASE)

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


@dataclass
class ExtractionContext:
    ticker: str
    filing_year: int
    company: str = ""
    cik: str = ""
    form: str = "10-K"
    filing_date: str = ""
    reporting_period: str = ""
    accession_number: str = ""
    source_url: str = ""
    section: str = "Cover"
    section_anchor: str | None = None
    blocks: list[dict] = field(default_factory=list)


def find_latest_local_filing(
    company_name: str,
    raw_directory: str | Path = "data/raw",
) -> tuple[int, Path]:
    """Return the newest locally downloaded normal 10-K for a company."""
    company_key = company_name.strip().lower()
    companies = COMPANIES
    if company_key not in companies:
        raise ValueError(f"Unknown company: {company_name}")

    company_directory = Path(raw_directory) / companies[company_key]["ticker"]
    filings = []
    for filing_path in company_directory.glob("*-10-K.html"):
        match = FILING_FILENAME_PATTERN.fullmatch(filing_path.name)
        if match:
            filings.append((int(match["year"]), filing_path))

    if not filings:
        raise FileNotFoundError(
            f"No normal 10-K HTML filing found in {company_directory}"
        )

    return max(filings, key=lambda filing: filing[0])


def load_latest_filing_html(
    company_name: str,
    raw_directory: str | Path = "data/raw",
) -> tuple[int, bytes]:
    """Load the newest local normal 10-K without requiring its filing year."""
    filing_year, filing_path = find_latest_local_filing(company_name, raw_directory)
    return filing_year, filing_path.read_bytes()


def parse_filing_html(html_content: bytes):
    return lxml_html.fromstring(html_content)


def extract_inline_xbrl_fact(root, fact_name: str) -> str:
    """Extract the first visible value for a named DEI Inline XBRL fact."""
    expected_name = fact_name.casefold()
    for node in root.xpath("//*[@name]"):
        node_name = node.get("name", "").split(":")[-1].casefold()
        if node_name == expected_name:
            return normalize_text(node.text_content())
    return ""


def normalize_iso_date(value: str) -> str:
    """Convert common filing date displays to YYYY-MM-DD when possible."""
    value = normalize_text(str(value or ""))
    if not value:
        return ""

    for date_format in ("%Y-%m-%d", "%B %d, %Y", "%B %d %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue
    return value


def load_extraction_metadata(
    filing_path: Path,
    root,
    company_info: dict[str, str],
    filing_year: int,
) -> dict[str, str | int]:
    """Combine configured identity, optional acquisition metadata, and DEI facts."""
    metadata_path_candidates = (
        filing_path.with_suffix(".metadata.json"),
        filing_path.parent / "metadata.json",
    )
    stored_metadata = {}
    for metadata_path in metadata_path_candidates:
        if not metadata_path.exists():
            continue
        with metadata_path.open(encoding="utf-8") as metadata_file:
            stored_metadata = json.load(metadata_file)
        if not isinstance(stored_metadata, dict):
            raise ValueError(f"Filing metadata must be a JSON object: {metadata_path}")
        break

    for identity_field in ("ticker", "cik"):
        stored_identity = str(stored_metadata.get(identity_field) or "")
        expected_identity = company_info[identity_field]
        if stored_identity and stored_identity != expected_identity:
            raise ValueError(
                f"Metadata {identity_field} {stored_identity!r} does not match "
                f"configured value {expected_identity!r}: {filing_path}"
            )

    stored_year = stored_metadata.get("filing_year")
    if stored_year is not None and int(stored_year) != filing_year:
        raise ValueError(
            f"Metadata filing year {stored_year} does not match filename year "
            f"{filing_year}: {filing_path}"
        )

    reporting_period = stored_metadata.get("reporting_period", "")
    if not reporting_period:
        reporting_period = extract_inline_xbrl_fact(root, "DocumentPeriodEndDate")

    document_type = extract_inline_xbrl_fact(root, "DocumentType")
    form = str(stored_metadata.get("form") or document_type or "10-K").upper()
    if form != "10-K":
        raise ValueError(f"Expected a normal 10-K, found form {form!r}: {filing_path}")

    metadata = {
        "company": stored_metadata.get("company") or company_info["company"],
        "ticker": stored_metadata.get("ticker") or company_info["ticker"],
        "cik": stored_metadata.get("cik") or company_info["cik"],
        "form": form,
        "filing_year": filing_year,
        "filing_date": normalize_iso_date(stored_metadata.get("filing_date", "")),
        "reporting_period": normalize_iso_date(reporting_period),
        "accession_number": str(stored_metadata.get("accession_number") or ""),
        "source_url": str(stored_metadata.get("source_url") or ""),
    }

    reporting_year = str(metadata["reporting_period"])[:4]
    if reporting_year.isdigit() and int(reporting_year) != filing_year:
        raise ValueError(
            f"Metadata reporting period {metadata['reporting_period']} does not match "
            f"filing filename year {filing_year}: {filing_path}"
        )
    return metadata


def raw_tag(node):
    return str(node.tag).lower() if isinstance(node.tag, str) else ""

def drop_non_text_nodes(root):
    title_nodes = root.xpath("//title")
    document_title = title_nodes[0].text_content().strip() if title_nodes else None

    for node in root.xpath("//script | //style | //noscript | //img | //svg | //picture"):
        node.drop_tree()

    for node in root.xpath("//head"):
        node.drop_tree()


def drop_hidden_nodes(root):
    for node in list(root.iter()):
        tag = raw_tag(node)
        style = "".join(node.get("style", "").lower().split())

        explicitly_hidden = (
                node.get("hidden") is not None
                or node.get("aria-hidden", "").lower() == "true"
                or "display:none" in style
                or "visibility:hidden" in style
                or tag == "ix:hidden"
                or tag.endswith("}hidden")
        )

        if explicitly_hidden:
            node.drop_tree()


def drop_xbrl_tags(root):
    for node in list(root.iter()):
        tag = raw_tag(node)

        if (
                tag in {"ix:nonnumeric", "ix:nonfraction", "ix:continuation"}
                or tag.endswith("}nonnumeric")
                or tag.endswith("}nonfraction")
                or tag.endswith("}continuation")
        ):
            node.drop_tag()

def print_tags(root):
    for node in list(root.iter()):
        print(raw_tag(node))

def normalize_text(text):
    text = html_lib.unescape(text)
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\xa0", " ")
    text = text.replace("\u00ad", "")
    text = text.replace("\u200b", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def compact_style(node) -> str:
    """Return inline CSS without insignificant spaces and with normalized case."""
    return "".join(node.get("style", "").lower().split())


def is_page_furniture(node) -> bool:
    """Conservatively identify repeated page navigation and printed page markers."""
    tag = raw_tag(node)
    text = normalize_text(node.text_content()) if hasattr(node, "text_content") else ""
    style = compact_style(node)

    if tag == "hr" and "page-break-after:always" in style:
        return True

    if "page-break-after:always" in style and not text:
        return True

    if text.casefold().strip() == "table of contents":
        internal_links = node.xpath(".//a[starts-with(@href, '#')]")
        if tag == "a" and node.get("href", "").startswith("#"):
            return True
        if internal_links:
            return True

    if PAGE_NUMBER_PATTERN.fullmatch(text):
        context_styles = [style]
        for ancestor in list(node.iterancestors())[:4]:
            context_styles.append(compact_style(ancestor))
        page_context = "".join(context_styles)
        mobileye_footer = (
            "text-align:center" in page_context
            and "margin:24pt0pt0pt0pt" in page_context
        )
        tesla_footer = (
            "text-align:center" in page_context
            and "position:absolute" in page_context
            and "bottom:0" in page_context
        )
        if mobileye_footer or tesla_footer:
            return True

    return False


def drop_page_furniture(root) -> int:
    """Remove recognized page furniture while preserving section anchors."""
    candidates = []
    for node in list(root.iter()):
        if not is_page_furniture(node):
            continue
        if any(ancestor in candidates for ancestor in node.iterancestors()):
            continue
        candidates.append(node)

    removed = 0
    for node in candidates:
        if node.getparent() is not None:
            node.drop_tree()
            removed += 1
    return removed


def is_leaf_content_div(node) -> bool:
    """Return True when a div contains text but no nested block container."""
    if raw_tag(node) != "div" or not normalize_text(node.text_content()):
        return False

    nested_block_tags = {"div", "p", "table", "ul", "ol", "li"}
    return not any(
        raw_tag(descendant) in nested_block_tags
        for descendant in node.iterdescendants()
    )


def find_source_anchor(node) -> str | None:
    """Find an ID on the node or on a nearby empty preceding sibling."""
    if node.get("id"):
        return node.get("id")

    previous = node.getprevious()
    for _ in range(5):
        if previous is None:
            break
        previous_text = normalize_text(previous.text_content())
        if previous_text:
            break
        if previous.get("id"):
            return previous.get("id")
        previous = previous.getprevious()
    return None


def text_excluding_descendants(node, excluded_tags: set[str]) -> str:
    """Collect text in order while omitting selected nested structures."""
    fragments = []

    def collect(current_node) -> None:
        if current_node.text:
            fragments.append(current_node.text)
        for child in current_node:
            if raw_tag(child) not in excluded_tags:
                collect(child)
            if child.tail:
                fragments.append(child.tail)

    collect(node)
    return normalize_text("".join(fragments))


def has_heading_style(node) -> bool:
    """Check whether a node has common SEC heading presentation signals."""
    for candidate in [node, *node.iterdescendants()]:
        tag = raw_tag(candidate)
        style = compact_style(candidate)
        if tag in {"b", "strong"}:
            return True
        if "font-weight:bold" in style or "font-weight:700" in style:
            return True
    return False


def identify_item_section(node, text: str) -> str | None:
    """Return a canonical major Item heading, or None for ordinary text."""
    if len(text) > 250:
        return None

    match = ITEM_HEADING_PATTERN.fullmatch(text)
    if not match:
        return None
    if not (has_heading_style(node) or text.isupper()):
        return None

    item_number = match.group("number").upper()
    item_title = match.group("title").strip().rstrip(".")
    return f"Item {item_number} — {item_title}"


def append_block(
    context: ExtractionContext,
    *,
    content_type: str,
    text: str,
    source_tag: str,
    source_anchor: str | None,
    extra: dict | None = None,
) -> dict:
    """Create one deterministic block and append it to the extraction context."""
    block_index = len(context.blocks) + 1
    block = {
        "block_id": f"{context.ticker}-{context.filing_year}-{block_index:06d}",
        "block_index": block_index,
        "company": context.company,
        "ticker": context.ticker,
        "cik": context.cik,
        "form": context.form,
        "filing_year": context.filing_year,
        "filing_date": context.filing_date,
        "reporting_period": context.reporting_period,
        "accession_number": context.accession_number,
        "section": context.section,
        "section_path": [context.section],
        "content_type": content_type,
        "text": text,
        "source_tag": source_tag,
        "source_anchor": source_anchor or context.section_anchor,
        "page_start": None,
        "page_end": None,
        "source_url": context.source_url,
    }
    if extra:
        block.update(extra)
    context.blocks.append(block)
    return block


def emit_paragraph(node, context: ExtractionContext) -> dict | None:
    """Emit one paragraph boundary, including all of its inline descendant text."""
    text = normalize_text(node.text_content())
    if not text:
        return None

    source_anchor = find_source_anchor(node)
    section = identify_item_section(node, text)
    content_type = "paragraph"
    if section:
        context.section = section
        context.section_anchor = source_anchor
        content_type = "heading"

    return append_block(
        context,
        content_type=content_type,
        text=text,
        source_tag=raw_tag(node),
        source_anchor=source_anchor,
    )


def emit_text_block(node, context: ExtractionContext) -> dict | None:
    """Emit a Tesla-style leaf div using paragraph semantics."""
    return emit_paragraph(node, context)


def emit_heading(node, context: ExtractionContext) -> dict | None:
    """Emit a semantic HTML heading and update the current Item when relevant."""
    text = normalize_text(node.text_content())
    if not text:
        return None

    source_anchor = find_source_anchor(node)
    section = identify_item_section(node, text)
    if section:
        context.section = section
        context.section_anchor = source_anchor

    return append_block(
        context,
        content_type="heading",
        text=text,
        source_tag=raw_tag(node),
        source_anchor=source_anchor,
    )


def emit_list_item(node, context: ExtractionContext) -> dict | None:
    text = text_excluding_descendants(node, {"ul", "ol"})
    if not text:
        return None
    return append_block(
        context,
        content_type="list_item",
        text=text,
        source_tag=raw_tag(node),
        source_anchor=find_source_anchor(node),
    )


def emit_table(node, context: ExtractionContext) -> dict | None:
    """Preserve a table as rows and text without interpreting its semantics yet."""
    rows = []
    for row in node.xpath(".//tr"):
        cells = [
            text_excluding_descendants(cell, {"table"})
            for cell in row.xpath("./th | ./td")
        ]
        if any(cells):
            rows.append(cells)

    if not rows:
        return None

    table_text = "\n".join(" | ".join(cells) for cells in rows)
    return append_block(
        context,
        content_type="unknown_table",
        text=table_text,
        source_tag="table",
        source_anchor=find_source_anchor(node),
        extra={"rows": rows},
    )


def visit_node(node, context: ExtractionContext) -> None:
    """Walk the DOM in order and emit each semantic boundary exactly once."""
    tag = raw_tag(node)

    if is_page_furniture(node):
        return

    if tag == "table":
        emit_table(node, context)
        return
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        emit_heading(node, context)
        return
    if tag == "p":
        emit_paragraph(node, context)
        return
    if tag == "li":
        emit_list_item(node, context)
        for nested_list in node.xpath("./ul | ./ol"):
            visit_node(nested_list, context)
        return
    if tag == "div" and is_leaf_content_div(node):
        emit_text_block(node, context)
        return

    for child in node:
        visit_node(child, context)


def extract_blocks(
    root,
    ticker: str,
    filing_year: int,
    metadata: dict[str, str | int] | None = None,
) -> list[dict]:
    """Extract ordered blocks from a cleaned filing DOM."""
    body = root.find("body")
    if body is None:
        raise ValueError("Parsed filing does not contain a body element")

    metadata = metadata or {}
    context = ExtractionContext(
        ticker=ticker,
        filing_year=filing_year,
        company=str(metadata.get("company", "")),
        cik=str(metadata.get("cik", "")),
        form=str(metadata.get("form", "10-K")),
        filing_date=str(metadata.get("filing_date", "")),
        reporting_period=str(metadata.get("reporting_period", "")),
        accession_number=str(metadata.get("accession_number", "")),
        source_url=str(metadata.get("source_url", "")),
    )
    visit_node(body, context)
    return context.blocks


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
    companies = COMPANIES
    if company_key not in companies:
        raise ValueError(f"Unknown company: {company_name}")

    filing_year, filing_path = find_latest_local_filing(company_key, raw_directory)
    document = parse_filing_html(filing_path.read_bytes())
    company_info = companies[company_key]
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
    companies = COMPANIES
    parser = argparse.ArgumentParser(
        description="Extract the latest local normal 10-K into structured JSONL blocks."
    )
    parser.add_argument("company", choices=sorted(companies))
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