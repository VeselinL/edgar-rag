import re

from .dom_processing import (
    PAGE_NUMBER_PATTERN,
    compact_style,
    has_heading_style,
    is_bold_element,
    is_page_furniture,
    normalize_text,
    raw_tag,
    text_excluding_descendants,
)


TABLE_BULLET_PATTERN = re.compile(r"^[•●▪◦○■□✓✔]$")
TABLE_NUMERIC_PATTERN = re.compile(
    r"^\(?[-+]?(?:[$€£¥]\s*)?(?:\d{1,3}(?:,\d{3})*|\d+)"
    r"(?:\.\d+)?\)?%?$"
)
TABLE_YEAR_PATTERN = re.compile(r"^(?:19|20)\d{2}$")
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
GENERIC_COMPANY_HEADER_PATTERN = re.compile(
    r"^[A-Z][A-Z0-9&.,'’ -]+\s(?:INC\.?|CORP(?:ORATION)?\.?|COMPANY|LTD\.?|PLC)$"
)
TABLE_CAPTION_PATTERN = re.compile(
    r"\b(?:was|were|is|are)\s+as\s+follows\b|\bthe\s+following\s+tables?\b",
    re.IGNORECASE,
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
            cell_elements = [cell, *cell.iterdescendants()]
            alignment = None
            for candidate in cell_elements:
                alignment = candidate.get("align", "").strip().casefold() or None
                if not alignment:
                    match = re.search(
                        r"(?:^|;)text-align:(left|right|center|justify)(?:;|$)",
                        compact_style(candidate),
                    )
                    alignment = match.group(1) if match else None
                if alignment:
                    break

            bottom_border = re.search(
                r"(?:^|;)border-bottom:([^;]+)", compact_style(cell)
            )
            cell_record = {
                "row": row_index,
                "column": column_index,
                "text": text,
                "rowspan": rowspan,
                "colspan": colspan,
                "source_tag": raw_tag(cell),
                "is_header": raw_tag(cell) == "th",
                "is_bold": any(
                    is_bold_element(candidate)
                    and normalize_text(candidate.text_content()) == text
                    for candidate in cell_elements
                ),
                "alignment": alignment,
                "has_bottom_border": bool(
                    bottom_border
                    and bottom_border.group(1) not in {"0", "none", "hidden"}
                ),
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
    return compact_value in {"—", "–"} or bool(
        compact_value and TABLE_NUMERIC_PATTERN.fullmatch(compact_value)
    )


def detect_table_header_rows(structure: dict) -> list[int]:
    """Treat every row before the first probable numeric data row as a header."""
    for row_index, row in enumerate(structure["rows"]):
        has_non_year_number = any(
            is_numeric_table_value(value)
            and not TABLE_YEAR_PATTERN.fullmatch(normalize_text(value))
            for value in row
            if value
        )
        if has_non_year_number:
            return list(range(row_index))
    return []


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


def extract_column_units(rows: list[list[str]]) -> list[str | None]:
    """Return units aligned to table columns when the markup exposes them."""
    column_count = max((len(row) for row in rows), default=0)
    units = [None] * column_count

    for row in rows:
        for column_index, value in enumerate(row):
            normalized = normalize_text(value)
            casefolded = normalized.casefold()
            explicit_unit = TABLE_UNITS_PATTERN.search(normalized)
            if explicit_unit:
                units[column_index] = normalize_text(explicit_unit.group(0))
            elif "dollar" in casefolded:
                units[column_index] = "dollars"
            elif "percent" in casefolded:
                units[column_index] = "percent"

            compact_value = normalized.replace(" ", "")
            if compact_value.startswith("$"):
                units[column_index] = "dollars"
                if (
                    compact_value == "$"
                    and column_index + 1 < len(row)
                    and is_numeric_table_value(row[column_index + 1])
                ):
                    units[column_index + 1] = "dollars"
            if compact_value == "%" or compact_value.endswith("%"):
                units[column_index] = "percent"
                if (
                    compact_value == "%"
                    and column_index > 0
                    and is_numeric_table_value(row[column_index - 1])
                ):
                    units[column_index - 1] = "percent"
    return units


def extract_table_units(
    rows: list[list[str]],
    column_units: list[str | None] | None = None,
) -> str | None:
    """Extract an explicit table unit such as 'U.S. dollars in millions'."""
    searchable_text = " ".join(
        value for row in rows[:6] for value in row if value
    )
    match = TABLE_UNITS_PATTERN.search(searchable_text)
    column_units = column_units or extract_column_units(rows)
    unit_kinds = set()
    for unit in column_units:
        if not unit:
            continue
        casefolded = unit.casefold()
        if "dollar" in casefolded:
            unit_kinds.add("dollars")
        elif "percent" in casefolded:
            unit_kinds.add("percent")
        elif "share" in casefolded or casefolded.startswith("in "):
            unit_kinds.add("shares")
    if len(unit_kinds) > 1:
        return "mixed"
    if match:
        return normalize_text(match.group(0))
    if unit_kinds == {"percent"}:
        return "percent"
    return None


def find_table_title(node) -> str | None:
    """Find a nearby heading or explicit prose caption for a table."""
    candidates = node.xpath(
        "preceding::*[self::p or self::div or self::h1 or self::h2 or "
        "self::h3 or self::h4 or self::h5 or self::h6][normalize-space()]"
    )
    nearby_candidates = []
    for candidate in reversed(candidates):
        if any(raw_tag(ancestor) == "table" for ancestor in candidate.iterancestors()):
            continue
        text = normalize_text(candidate.text_content())
        if not text:
            continue
        is_page_header = (
            is_page_furniture(candidate)
            or text.casefold().startswith("table of contents")
            or GENERIC_FINANCIAL_HEADER_PATTERN.fullmatch(text)
            or GENERIC_COMPANY_HEADER_PATTERN.fullmatch(text)
        )
        if is_page_header:
            break

        if raw_tag(candidate) == "div" and any(
            raw_tag(descendant) in {"div", "p", "table"}
            for descendant in candidate.iterdescendants()
        ):
            continue
        nearby_candidates.append((candidate, text))
        if len(nearby_candidates) == 12:
            break

    if not nearby_candidates:
        return None

    nearest_text = nearby_candidates[0][1]
    if TABLE_CAPTION_PATTERN.search(nearest_text):
        for _, text in nearby_candidates[1:4]:
            is_short_label = (
                len(text.split()) <= 8
                and not text.endswith((".", ":", ";", "?", "!"))
            )
            if is_short_label:
                return text.rstrip(":")
        return nearest_text.rstrip(":")

    for candidate, text in nearby_candidates[:8]:
        if len(text) <= 250 and (has_heading_style(candidate) or text.isupper()):
            return text.rstrip(":")
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
    lines.extend(" | ".join(row) for row in rows if any(row))
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
