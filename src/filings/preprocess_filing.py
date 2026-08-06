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

from .fetch_data import COMPANIES

from lxml import html as lxml_html

FILING_FILENAME_PATTERN = re.compile(r"(?P<year>\d{4})-10-K\.html$")
ITEM_HEADING_PATTERN = re.compile(
    r"^item\s+(?P<number>\d{1,2}[a-z]?)\s*[.\-—:]?\s*(?P<title>.+?)\.?$",
    re.IGNORECASE,
)
PAGE_NUMBER_PATTERN = re.compile(r"^(?:\d{1,3}|[ivxlcdm]{1,8})$", re.IGNORECASE)
TABLE_BULLET_PATTERN = re.compile(r"^[•●▪◦○■□✓✔]$")
TABLE_NUMERIC_PATTERN = re.compile(
    r"^\(?[-+]?(?:[$€£¥]\s*)?(?:\d{1,3}(?:,\d{3})*|\d+)"
    r"(?:\.\d+)?\)?%?$"
)
TABLE_PERIOD_PATTERN = re.compile(
    r"\b(?:19|20)\d{2}\b|\b(?:year|years)\s+ended\b|\bas\s+of\b|"
    r"\b(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+\d{1,2},?\b",
    re.IGNORECASE,
)
TABLE_UNITS_PATTERN = re.compile(
    r"\b(?:(?:u\.s\.\s+)?dollars?|shares?)\s+in\s+"
    r"(?:thousands|millions|billions)\b|\bin\s+(?:thousands|millions|billions)\b",
    re.IGNORECASE,
)
TABLE_FINANCIAL_PATTERN = re.compile(
    r"\b(?:assets?|liabilit(?:y|ies)|revenues?|income|loss|expenses?|cash|equity|"
    r"earnings?|shares?|tax(?:es)?|debt|inventory|inventories|receivables?|"
    r"fair value)\b",
    re.IGNORECASE,
)
TABLE_EXHIBIT_PATTERN = re.compile(
    r"\b(?:exhibit\s+no\.?|exhibits and financial statement schedules)\b",
    re.IGNORECASE,
)
GENERIC_FINANCIAL_HEADER_PATTERN = re.compile(
    r"^notes to (?:the )?consolidated financial statements$",
    re.IGNORECASE,
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


COVER_SPECIFIC_FACTS = {
    "exchange": "SecurityExchangeName",
    "address": "EntityAddressAddressLine1",
    "city": "EntityAddressCityOrTown",
    "country": "EntityAddressCountry",
    "postal_code": "EntityAddressPostalZipCode",
}


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
    subsection: str | None = None
    section_anchor: str | None = None
    blocks: list[dict] = field(default_factory=list)
    content_started: bool = False


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

    cover_metadata = {
        key: extract_inline_xbrl_fact(root, fact) for key, fact in COVER_SPECIFIC_FACTS.items()
    }

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
    metadata = metadata | cover_metadata

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
    """Remove DOM nodes with non-text tags."""
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
        if is_bold_element(candidate):
            return True
    return False


def is_bold_element(node) -> bool:
    """Return True when an element explicitly renders its complete text as bold."""
    style = compact_style(node)
    return (
        raw_tag(node) in {"b", "strong"}
        or "font-weight:bold" in style
        or "font-weight:700" in style
    )


def is_subheading_candidate(node, text: str) -> bool:
    """Conservatively detect a short standalone block whose full text is bold."""
    if raw_tag(node) not in {"p", "div"} or not text or len(text) > 200:
        return False
    if len(text.split()) > 25 or text.endswith((".", "?", "!", ";")):
        return False
    if any(raw_tag(ancestor) in {"table", "li"} for ancestor in node.iterancestors()):
        return False
    if node.xpath(".//a[starts-with(@href, '#')]"):
        return False

    return any(
        is_bold_element(candidate)
        and normalize_text(candidate.text_content()) == text
        for candidate in [node, *node.iterdescendants()]
    )


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
    section_path = [context.section]
    if context.subsection:
        section_path.append(context.subsection)

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
        "section_path": section_path,
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
    extra = None
    if section:
        context.section = section
        context.subsection = None
        context.section_anchor = source_anchor
        content_type = "heading"
        extra = {"heading_kind": "item", "heading_level": 1}
    elif is_subheading_candidate(node, text):
        context.subsection = text
        content_type = "heading"
        extra = {"heading_kind": "subsection", "heading_level": 2}

    return append_block(
        context,
        content_type=content_type,
        text=text,
        source_tag=raw_tag(node),
        source_anchor=source_anchor,
        extra=extra,
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
        context.subsection = None
        context.section_anchor = source_anchor
        heading_kind = "item"
        heading_level = 1
    else:
        context.subsection = text
        heading_kind = "subsection"
        heading_level = 2

    return append_block(
        context,
        content_type="heading",
        text=text,
        source_tag=raw_tag(node),
        source_anchor=source_anchor,
        extra={"heading_kind": heading_kind, "heading_level": heading_level},
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


def parse_table_span(value: str | None) -> int:
    """Return a safe positive rowspan or colspan value."""
    try:
        return max(1, int(value or "1"))
    except ValueError:
        return 1


def table_rows(node) -> list:
    """Return rows belonging to this table, excluding rows in nested tables."""
    rows = []
    for row in node.xpath(".//tr"):
        nearest_table = next(
            (ancestor for ancestor in row.iterancestors() if raw_tag(ancestor) == "table"),
            None,
        )
        if nearest_table is node:
            rows.append(row)
    return rows


def extract_table_structure(node) -> dict:
    """Extract raw cells and a span-aware rectangular grid from a table."""
    raw_rows = []
    raw_cells = []
    slots = {}
    source_rows = table_rows(node)

    for row_index, row in enumerate(source_rows):
        physical_row = []
        column_index = 0
        for cell in row.xpath("./th | ./td"):
            while (row_index, column_index) in slots:
                column_index += 1

            text = text_excluding_descendants(cell, {"table"})
            rowspan = parse_table_span(cell.get("rowspan"))
            colspan = parse_table_span(cell.get("colspan"))
            cell_record = {
                "row": row_index,
                "column": column_index,
                "text": text,
                "rowspan": rowspan,
                "colspan": colspan,
                "source_tag": raw_tag(cell),
                "is_header": raw_tag(cell) == "th",
            }
            raw_cells.append(cell_record)
            physical_row.append(text)

            for row_offset in range(rowspan):
                covered_row = row_index + row_offset
                if covered_row >= len(source_rows):
                    break
                for column_offset in range(colspan):
                    covered_column = column_index + column_offset
                    slots[(covered_row, covered_column)] = {
                        "text": text,
                        "origin_row": row_index,
                        "origin_column": column_index,
                    }
            column_index += colspan
        raw_rows.append(physical_row)

    if not source_rows or not slots:
        return {
            "raw_rows": raw_rows,
            "raw_cells": raw_cells,
            "rows": [],
            "expanded_rows": [],
            "source_row_indexes": [],
            "source_column_indexes": [],
        }

    maximum_column = max(column for _, column in slots)
    nonempty_rows = [
        row_index
        for row_index in range(len(source_rows))
        if any(
            slots.get((row_index, column), {}).get("text", "")
            for column in range(maximum_column + 1)
        )
    ]
    nonempty_columns = [
        column
        for column in range(maximum_column + 1)
        if any(
            slots.get((row_index, column), {}).get("text", "")
            for row_index in nonempty_rows
        )
    ]

    rows = []
    expanded_rows = []
    for row_index in nonempty_rows:
        display_row = []
        expanded_row = []
        for column in nonempty_columns:
            slot = slots.get((row_index, column))
            expanded_row.append(slot["text"] if slot else "")
            if slot and (
                slot["origin_row"] == row_index
                and slot["origin_column"] == column
            ):
                display_row.append(slot["text"])
            else:
                display_row.append("")
        rows.append(display_row)
        expanded_rows.append(expanded_row)

    return {
        "raw_rows": raw_rows,
        "raw_cells": raw_cells,
        "rows": rows,
        "expanded_rows": expanded_rows,
        "source_row_indexes": nonempty_rows,
        "source_column_indexes": nonempty_columns,
    }


def is_numeric_table_value(value: str) -> bool:
    """Return True for a standalone financial-style numeric value."""
    compact_value = normalize_text(value).replace(" ", "")
    return bool(compact_value and TABLE_NUMERIC_PATTERN.fullmatch(compact_value))


def detect_table_header_rows(structure: dict) -> list[int]:
    """Identify explicit and common multi-row financial header rows."""
    source_to_clean = {
        source_index: clean_index
        for clean_index, source_index in enumerate(structure["source_row_indexes"])
    }
    header_rows = {
        source_to_clean[cell["row"]]
        for cell in structure["raw_cells"]
        if cell["is_header"]
        and cell["text"]
        and cell["row"] in source_to_clean
    }

    for row_index, row in enumerate(structure["expanded_rows"][:5]):
        row_text = " ".join(value for value in row if value)
        if TABLE_PERIOD_PATTERN.search(row_text) or TABLE_UNITS_PATTERN.search(row_text):
            header_rows.add(row_index)

    return sorted(header_rows)


def build_column_headers(structure: dict, header_rows: list[int]) -> list[str]:
    """Combine multi-row header labels for every logical table column."""
    if not structure["expanded_rows"]:
        return []

    headers = []
    for column_index in range(len(structure["expanded_rows"][0])):
        labels = []
        for row_index in header_rows:
            label = structure["expanded_rows"][row_index][column_index]
            if label and label not in labels:
                labels.append(label)
        headers.append(" — ".join(labels))
    return headers


def extract_table_units(rows: list[list[str]]) -> str | None:
    """Extract an explicit table unit such as 'U.S. dollars in millions'."""
    searchable_text = " ".join(
        value for row in rows[:6] for value in row if value
    )
    match = TABLE_UNITS_PATTERN.search(searchable_text)
    if match:
        return normalize_text(match.group(0))
    if any(value.strip() == "%" or value.strip().endswith("%") for row in rows for value in row):
        return "percent"
    return None


def find_table_title(node) -> str | None:
    """Find a nearby styled title without treating arbitrary prose as a caption."""
    candidates = node.xpath(
        "preceding::*[self::p or self::div or self::h1 or self::h2 or "
        "self::h3 or self::h4 or self::h5 or self::h6][normalize-space()]"
    )
    for candidate in reversed(candidates[-12:]):
        if any(raw_tag(ancestor) == "table" for ancestor in candidate.iterancestors()):
            continue
        text = normalize_text(candidate.text_content())
        if not text or len(text) > 250 or is_page_furniture(candidate):
            continue
        if GENERIC_FINANCIAL_HEADER_PATTERN.fullmatch(text):
            continue
        if has_heading_style(candidate) or text.isupper():
            return text
    return None


def classify_table(node, structure: dict, section: str) -> tuple[str, list[str]]:
    """Conservatively classify a table using several independent signals."""
    rows = structure["rows"]
    values = [value for row in rows for value in row if value]
    table_text = " ".join(values)
    table_text_casefolded = table_text.casefold()
    reasons = []

    item_references = len(re.findall(r"\bitem\s+\d{1,2}[a-z]?\.?", table_text, re.I))
    link_count = len(node.xpath(".//a[@href]"))
    page_header = any(
        value.casefold() == "page" for row in rows[:3] for value in row if value
    )
    page_number_rows = sum(
        any(PAGE_NUMBER_PATTERN.fullmatch(value) for value in row if value)
        for row in rows[1:]
    )
    if (
        "table of contents" in table_text_casefolded
        or (page_header and item_references >= 3)
        or (page_header and page_number_rows >= 3)
        or (link_count >= 3 and item_references >= 2)
    ):
        reasons.append("contains table-of-contents navigation signals")
        return "navigation", reasons

    bullet_rows = 0
    for row in rows:
        nonempty_values = [value for value in row if value]
        if any(TABLE_BULLET_PATTERN.fullmatch(value) for value in nonempty_values):
            bullet_rows += 1
    if rows and bullet_rows / len(rows) >= 0.6:
        reasons.append(f"{bullet_rows} of {len(rows)} rows contain bullet markers")
        return "list", reasons

    if section.casefold().startswith("item 15") or TABLE_EXHIBIT_PATTERN.search(table_text):
        reasons.append("contains exhibit-index references")
        return "text", reasons

    numeric_count = sum(is_numeric_table_value(value) for value in values)
    numeric_row_count = sum(
        any(is_numeric_table_value(value) for value in row if value)
        for row in rows[1:]
    )
    has_period = bool(TABLE_PERIOD_PATTERN.search(table_text))
    has_units = bool(TABLE_UNITS_PATTERN.search(table_text))
    has_percent_units = any(
        value.strip() == "%" or value.strip().endswith("%") for value in values
    )
    has_financial_terms = bool(TABLE_FINANCIAL_PATTERN.search(table_text))
    has_explicit_headers = any(cell["is_header"] for cell in structure["raw_cells"])
    data_score = sum(
        (
            numeric_count >= 2,
            has_period,
            has_units or has_percent_units,
            has_financial_terms,
            has_explicit_headers,
        )
    )
    structured_numeric_grid = len(rows) >= 3 and numeric_row_count >= 2
    if (
        len(rows) >= 2
        and len(structure["source_column_indexes"]) >= 2
        and (data_score >= 3 or structured_numeric_grid)
    ):
        reasons.extend(
            reason
            for condition, reason in (
                (numeric_count >= 2, f"contains {numeric_count} numeric cells"),
                (structured_numeric_grid, "contains numeric values across multiple rows"),
                (has_period, "contains reporting-period labels"),
                (has_units or has_percent_units, "contains explicit units"),
                (has_financial_terms, "contains financial row labels"),
                (has_explicit_headers, "contains HTML header cells"),
            )
            if condition
        )
        return "data", reasons

    if values and numeric_count == 0:
        reasons.append("contains text without a numeric data grid")
        return "text", reasons

    reasons.append("did not meet a conservative classification threshold")
    return "unknown", reasons


def render_table_text(
    rows: list[list[str]],
    *,
    title: str | None = None,
    units: str | None = None,
) -> str:
    """Create compact retrieval text while retaining the aligned grid separately."""
    lines = []
    if title:
        lines.append(title)
    if units and (not title or units.casefold() not in title.casefold()):
        lines.append(units)
    lines.extend(" | ".join(value for value in row if value) for row in rows)
    return "\n".join(line for line in lines if line)


def group_list_rows(rows: list[list[str]]) -> list[dict]:
    """Group bullet-table rows into semantic list items."""
    items = []
    for row_index, row in enumerate(rows):
        values = [value for value in row if value]
        has_bullet = any(TABLE_BULLET_PATTERN.fullmatch(value) for value in values)
        text = " ".join(
            value for value in values if not TABLE_BULLET_PATTERN.fullmatch(value)
        )
        if not text:
            continue
        if has_bullet or not items:
            items.append({"text": text, "row_indexes": [row_index]})
        else:
            items[-1]["text"] = normalize_text(f"{items[-1]['text']} {text}")
            items[-1]["row_indexes"].append(row_index)
    return items


def emit_table(node, context: ExtractionContext) -> dict | list[dict] | None:
    """Classify and emit a span-aware table or its semantic list items."""
    structure = extract_table_structure(node)
    rows = structure["rows"]
    if not rows:
        return None

    table_class, classification_reasons = classify_table(
        node,
        structure,
        context.section,
    )
    source_anchor = find_source_anchor(node)

    if table_class == "list":
        blocks = []
        for item in group_list_rows(rows):
            blocks.append(
                append_block(
                    context,
                    content_type="list_item",
                    text=item["text"],
                    source_tag="table",
                    source_anchor=source_anchor,
                    extra={
                        "table_class": table_class,
                        "classification_reasons": classification_reasons,
                        "table_row_indexes": item["row_indexes"],
                        "rows": [rows[index] for index in item["row_indexes"]],
                    },
                )
            )
        return blocks

    header_rows = detect_table_header_rows(structure)
    title = find_table_title(node) if table_class == "data" else None
    units = extract_table_units(rows) if table_class == "data" else None
    content_type = {
        "navigation": "navigation",
        "data": "data_table",
        "text": "text_table",
        "unknown": "unknown_table",
    }[table_class]
    extra = {
        "table_class": table_class,
        "classification_reasons": classification_reasons,
        "raw_rows": structure["raw_rows"],
        "raw_cells": structure["raw_cells"],
        "rows": rows,
    }
    if table_class == "data":
        extra.update(
            {
                "title": title,
                "units": units,
                "header_row_indexes": header_rows,
                "column_headers": build_column_headers(structure, header_rows),
                "data_rows": [
                    row for index, row in enumerate(rows) if index not in header_rows
                ],
            }
        )

    return append_block(
        context,
        content_type=content_type,
        text=render_table_text(rows, title=title, units=units),
        source_tag="table",
        source_anchor=source_anchor,
        extra=extra,
    )


def visit_node(node, context: ExtractionContext) -> None:
    """Walk the DOM in order and emit each semantic boundary exactly once."""
    tag = raw_tag(node)

    if is_page_furniture(node):
        return

    if tag == "table":
        if context.content_started:
            emit_table(node, context)
        return

    is_text_block = (
            tag in {"p", "h1", "h2", "h3", "h4", "h5", "h6"}
            or (tag == "div" and is_leaf_content_div(node))
    )

    if is_text_block and not context.content_started:
        text = normalize_text(node.text_content())
        section = identify_item_section(node, text)
        is_toc_link = bool(node.xpath(".//a[starts-with(@href, '#')]"))

        if section and section.startswith("Item 1 —") and not is_toc_link:
            context.content_started = True
        else:
            return

    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        emit_heading(node, context)
        return
    if tag == "p":
        emit_paragraph(node, context)
        return
    if tag == "li":
        if not context.content_started:
            return

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
