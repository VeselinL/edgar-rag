import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from types import MappingProxyType

import lxml
from lxml import html as lxml_html

from .dom_processing import (
    PAGE_NUMBER_PATTERN,
    collect_visible_text,
    compact_style,
    has_heading_style,
    is_bold_element,
    normalize_text,
    raw_tag,
    text_excluding_descendants,
)


TABLE_SCHEMA_VERSION = 2
TABLE_HEURISTICS_VERSION = "sec-logical-v2"
HTML_TABLE_FINGERPRINT_VERSION = "cleaned-lxml-html-v1"
TABLE_HEURISTICS = MappingProxyType(
    {
        "header_scan_max_nonempty_rows": 8,
        "header_min_objective_margin_over_headerless": 2.0,
        "header_tie_epsilon": 0.25,
        "lane_interval_overlap_coefficient": 0.50,
        "lane_min_support_rows": 2,
        "lane_min_support_fraction": 0.25,
        "title_lookback_max_meaningful_blocks": 6,
        # A reviewed native caption is one 535-character sentence. The bounded
        # 600-character limit retains it without admitting the genuinely
        # overlong multi-sentence prose rejected by the selector.
        "title_caption_max_characters": 600,
        "title_heading_max_characters": 200,
        "continuation_max_intervening_meaningful_blocks": 0,
        "continuation_row_label_overlap": 0.80,
        "continuation_header_path_similarity": 0.80,
        "index_min_record_rows": 3,
        "index_page_value_fraction": 0.80,
        "index_text_label_fraction": 0.70,
        "structured_min_record_rows": 2,
        "structured_signature_support_fraction": 0.70,
    }
)

TABLE_BULLET_PATTERN = re.compile(r"^[•●▪◦○■□✓✔]$")
TABLE_YEAR_PATTERN = re.compile(r"^(?:19|20)\d{2}$")
TABLE_PERIOD_PATTERN = re.compile(
    r"\b(?:19|20)\d{2}\b|\b(?:year|years|months|quarters?)\s+ended\b|"
    r"\bas\s+of\b|\b(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|"
    r"sept|oct|nov|dec)\.?\s+\d{1,2},?(?:\s+(?:19|20)\d{2})?\b",
    re.IGNORECASE,
)
TABLE_UNITS_PATTERN = re.compile(
    r"\b(?:(?:u\.s\.?|us)\s+)?dollars?\s+in\s+"
    r"(?:thousands|millions|billions)\b|"
    r"\b(?:shares?|vehicles?|units?)\s+in\s+(?:thousands|millions|billions)\b|"
    r"\b(?:amounts?\s+)?in\s+(?:thousands|millions|billions)\b",
    re.IGNORECASE,
)
UNIT_ONLY_PATTERN = re.compile(
    r"^\(?\s*(?:(?:u\.s\.?|us)\s+)?(?:dollars?|shares?|vehicles?|amounts?)?"
    r"\s*(?:in\s+)?(?:thousands|millions|billions|percent)\s*\)?$",
    re.IGNORECASE,
)
TABLE_FINANCIAL_PATTERN = re.compile(
    r"\b(?:assets?|liabilit(?:y|ies)|revenues?|income|loss|expenses?|cash|equity|"
    r"earnings?|shares?|tax(?:es)?|debt|inventory|inventories|receivables?|"
    r"fair value|lease|payments?|maturit(?:y|ies)|principal|interest|ebit|"
    r"operating|automotive|market share|hedg(?:e|ed|ing)|notional)\b",
    re.IGNORECASE,
)
TABLE_EXHIBIT_PATTERN = re.compile(
    r"\b(?:exhibit\s+(?:no\.?|number|description)|file\s+number|filing\s+date)\b",
    re.IGNORECASE,
)
TABLE_CAPTION_PATTERN = re.compile(
    r"\b(?:following\s+tables?|tables?\s+below|"
    r"tables?\s+(?:summari[sz]es?|presents?)|"
    r"(?:summari[sz]ed|presented)\s+in\s+(?:the\s+)?(?:following\s+)?table|"
    r"were\s+as\s+follows|was\s+as\s+follows|are\s+as\s+follows|"
    r"is\s+as\s+follows)\b",
    re.IGNORECASE,
)
GENERIC_FINANCIAL_HEADER_PATTERN = re.compile(
    r"^notes?\s+to\s+(?:the\s+)?(?:consolidated\s+)?financial\s+statements"
    r"(?:\s*[\-\u2012\u2013\u2014]\s*)?(?:\(continued\)|continued)?$",
    re.IGNORECASE,
)
GENERIC_COMPANY_HEADER_PATTERN = re.compile(
    r"^[A-Z][A-Z0-9&.,'’ -]+\s(?:AND\s+SUBSIDIARIES|INC\.?|"
    r"CORP(?:ORATION)?\.?|COMPANY|LTD\.?|PLC)$"
)
PAGE_LABEL_PATTERN = re.compile(r"^(?:[A-Z]-)?\d{1,3}$", re.IGNORECASE)

NUMERIC_CORE = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
DATE_NUMERIC_PATTERN = re.compile(
    r"^(?:\d{1,2}/\d{1,2}/(?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2})$"
)
DATE_MONTH_PATTERN = re.compile(
    r"^(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|"
    r"nov|dec)\.?\s+\d{1,2},?\s+(?:19|20)\d{2}$",
    re.IGNORECASE,
)
DURATION_PATTERN = re.compile(
    r"^(?P<start>\d+(?:\.\d+)?)\s*(?P<start_unit>days?|months?|years?)?"
    r"(?:\s*(?:-|to)\s*(?P<end>\d+(?:\.\d+)?)\s*)?"
    r"(?P<end_unit>days?|months?|years?)$",
    re.IGNORECASE,
)
SEC_FILE_NUMBER_PATTERN = re.compile(r"^\d{3}-\d{5,}$")
EXHIBIT_IDENTIFIER_PATTERN = re.compile(r"^\d{1,3}(?:\.\d{1,4})+[A-Za-z+*†‡-]*$")
EXHIBIT_RECORD_IDENTIFIER_PATTERN = re.compile(
    r"^(?:exhibit\s+)?(?:\d{1,3}(?:\.\d{1,4})+|\d{1,3}-[A-Z0-9-]+)"
    r"[A-Za-z+*†‡-]*$",
    re.IGNORECASE,
)
PAGE_IDENTIFIER_PATTERN = re.compile(r"^(?:[A-Z]{1,3}-\d{1,3}|\d{1,3})$", re.I)
FOOTNOTE_ONLY_PATTERN = re.compile(r"^(?:[*†‡]+|\(\d+\))$")
FOOTNOTE_SUFFIX_PATTERN = re.compile(r"(?P<suffix>[*†‡]+|\(\d+\))$")
CURRENCY_MARKERS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
MISSING_MARKERS = {"—", "–", "-"}
MISSING_TEXT_PATTERN = re.compile(r"^(?:n/?a|n\.?m\.?|not\s+meaningful|not\s+applicable)$", re.I)
NUMERIC_KINDS = {
    "missing_numeric",
    "numeric_scalar",
    "numeric_range",
    "percentage",
    "percentage_range",
    "year",
    "year_range",
    "date",
    "duration",
}


def parse_table_span(value: str | None) -> int:
    """Return a safe positive rowspan or colspan value."""
    try:
        return max(1, int(value or "1"))
    except (TypeError, ValueError):
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


def table_fingerprint(node) -> str:
    serialized = lxml_html.tostring(
        node,
        encoding="utf-8",
        method="html",
        with_tail=False,
    )
    return "sha256:" + hashlib.sha256(serialized).hexdigest()


def _visible_border(style_value: str | None) -> bool:
    if not style_value:
        return False
    value = style_value.strip().casefold()
    tokens = re.split(r"\s+", value)
    if any(token in {"none", "hidden"} for token in tokens):
        return False
    widths = re.findall(r"(?<![\w.])(-?(?:\d+(?:\.\d*)?|\.\d+))(px|pt|em|rem)?", value)
    if widths and all(float(number) == 0 for number, _ in widths):
        return False
    return bool(widths or any(token in {"solid", "double", "dashed", "dotted"} for token in tokens))


def _cell_bold_text_ratio(cell) -> float:
    total = 0
    bold = 0

    def add(value: str | None, is_bold: bool) -> None:
        nonlocal total, bold
        count = sum(not character.isspace() for character in value or "")
        total += count
        if is_bold:
            bold += count

    def visit(node, inherited_bold: bool = False) -> None:
        active_bold = inherited_bold or is_bold_element(node)
        add(node.text, active_bold)
        for child in node:
            if raw_tag(child) != "table":
                visit(child, active_bold)
            add(child.tail, active_bold)

    visit(cell)
    return bold / total if total else 0.0


def extract_table_structure(node, html_table_id: str | None = None) -> dict:
    """Preserve raw cells and reconstruct the existing span-aware physical grid."""
    raw_rows: list[list[str]] = []
    raw_cells: list[dict] = []
    slots: dict[tuple[int, int], dict] = {}
    source_rows = table_rows(node)

    for row_index, row in enumerate(source_rows):
        raw_row = []
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

            border_values = []
            for candidate in cell_elements:
                match = re.search(
                    r"(?:^|;)border-bottom:([^;]+)", compact_style(candidate)
                )
                if match:
                    border_values.append(match.group(1))
            raw_cell_id = (
                f"{html_table_id}-R{row_index}-C{column_index}"
                if html_table_id
                else f"HTMLTABLE-R{row_index}-C{column_index}"
            )
            cell_record = {
                "raw_cell_id": raw_cell_id,
                "row": row_index,
                "column": column_index,
                "physical_start": column_index,
                "physical_end": column_index + colspan,
                "text": text,
                "rowspan": rowspan,
                "colspan": colspan,
                "source_tag": raw_tag(cell),
                "is_header": raw_tag(cell) == "th",
                "is_bold": _cell_bold_text_ratio(cell) >= 0.999,
                "bold_text_ratio": _cell_bold_text_ratio(cell),
                "alignment": alignment,
                "has_bottom_border": any(_visible_border(value) for value in border_values),
                "xbrl_unit_refs": [
                    value
                    for value in cell.get("data-sec-xbrl-unitrefs", "").split("|")
                    if value
                ],
                "xbrl_scales": [
                    value
                    for value in cell.get("data-sec-xbrl-scales", "").split("|")
                    if value
                ],
            }
            raw_cells.append(cell_record)
            raw_row.append(text)

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
        raw_rows.append(raw_row)

    if not source_rows or not slots:
        result = {
            "raw_rows": raw_rows,
            "raw_cells": raw_cells,
            "physical_rows": [],
            "physical_expanded_rows": [],
            "physical_source_row_indexes": [],
            "physical_source_column_indexes": [],
        }
        return result | {
            "rows": result["physical_rows"],
            "expanded_rows": result["physical_expanded_rows"],
            "source_row_indexes": result["physical_source_row_indexes"],
            "source_column_indexes": result["physical_source_column_indexes"],
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

    physical_rows = []
    physical_expanded_rows = []
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
        physical_rows.append(display_row)
        physical_expanded_rows.append(expanded_row)

    result = {
        "raw_rows": raw_rows,
        "raw_cells": raw_cells,
        "physical_rows": physical_rows,
        "physical_expanded_rows": physical_expanded_rows,
        "physical_source_row_indexes": nonempty_rows,
        "physical_source_column_indexes": nonempty_columns,
    }
    # Deprecated aliases deliberately remain physical so stale artifacts are visible.
    return result | {
        "rows": physical_rows,
        "expanded_rows": physical_expanded_rows,
        "source_row_indexes": nonempty_rows,
        "source_column_indexes": nonempty_columns,
    }


def _detection_text(text: str) -> str:
    value = normalize_text(text)
    value = value.replace("−", "-").replace("–", "-").replace("—", "-")
    return value


def _numeric_scalar_details(value: str) -> dict | None:
    compact = value.strip()
    accounting_negative = False
    percent = compact.endswith("%")
    if percent:
        compact = compact[:-1].strip()
    currency_marker = compact[:1] if compact[:1] in CURRENCY_MARKERS else None
    if currency_marker:
        compact = compact[1:].strip()
    sign = 1
    if compact.startswith(("+", "-")):
        if compact[0] == "-":
            sign = -1
        compact = compact[1:].strip()
    if compact.startswith("(") and compact.endswith(")"):
        accounting_negative = True
        sign = -1
        compact = compact[1:-1].strip()
    if not re.fullmatch(NUMERIC_CORE, compact):
        return None
    number = float(compact.replace(",", "")) * sign
    return {
        "numeric_value": number,
        "currency_marker": currency_marker,
        "currency_code": CURRENCY_MARKERS.get(currency_marker),
        "percent": percent,
        "accounting_negative": accounting_negative,
    }


def _split_typed_range(value: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"(.+?)\s+(?:-|to)\s+(.+)", value, re.I)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    match = re.fullmatch(r"(.+?\d(?:%?))-(?=[$€£¥]?\d)([$€£¥]?\d.*)", value)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return None


def analyze_cell_lexically(text: str) -> dict:
    """Return non-destructive lexical candidates and typed-value features."""
    display = normalize_text(text)
    detection = _detection_text(display)
    result = {
        "display_text": display,
        "detection_normalized_text": detection,
        "candidate_kinds": [],
        "refined_kind": "text",
        "numeric_value": None,
        "range_start": None,
        "range_end": None,
        "currency_code": None,
        "currency_marker": None,
        "scale": None,
        "percent": False,
        "accounting_negative": False,
        "missing_value": False,
        "footnote_suffix": None,
        "identifier_pattern_features": [],
        "confidence": 1.0,
        "reason_codes": [],
    }
    if not display:
        result.update(refined_kind="empty", candidate_kinds=["empty"], reason_codes=["empty"])
        return result
    if display in MISSING_MARKERS:
        result.update(
            refined_kind="missing_numeric",
            candidate_kinds=["missing_numeric"],
            missing_value=True,
            reason_codes=["missing_marker"],
        )
        return result
    if MISSING_TEXT_PATTERN.fullmatch(display):
        result.update(
            refined_kind="missing_numeric",
            candidate_kinds=["missing_numeric"],
            missing_value=True,
            reason_codes=["textual_missing_marker"],
        )
        return result
    if display in CURRENCY_MARKERS:
        result.update(
            refined_kind="currency_marker",
            candidate_kinds=["currency_marker"],
            currency_marker=display,
            currency_code=CURRENCY_MARKERS[display],
            reason_codes=["currency_marker"],
        )
        return result
    if display == "%":
        result.update(
            refined_kind="percent_marker",
            candidate_kinds=["percent_marker"],
            percent=True,
            reason_codes=["percent_marker"],
        )
        return result
    compact_display = display.replace(" ", "")
    if compact_display.endswith("%") and compact_display[:-1] in MISSING_MARKERS:
        result.update(
            refined_kind="percentage",
            candidate_kinds=["percentage", "missing_numeric"],
            percent=True,
            missing_value=True,
            reason_codes=["missing_percent_value"],
        )
        return result
    if (
        compact_display[:1] in CURRENCY_MARKERS
        and compact_display[1:] in MISSING_MARKERS
    ):
        marker = compact_display[0]
        result.update(
            refined_kind="missing_numeric",
            candidate_kinds=["missing_numeric"],
            currency_marker=marker,
            currency_code=CURRENCY_MARKERS[marker],
            missing_value=True,
            reason_codes=["currency_missing_value"],
        )
        return result
    if re.fullmatch(r"[*†‡]+", display):
        result.update(
            refined_kind="footnote_marker",
            candidate_kinds=["footnote_marker"],
            footnote_suffix=display,
            reason_codes=["footnote_marker"],
        )
        return result
    if DATE_NUMERIC_PATTERN.fullmatch(detection) or DATE_MONTH_PATTERN.fullmatch(detection):
        result.update(refined_kind="date", candidate_kinds=["date"], reason_codes=["date_grammar"])
        return result
    if SEC_FILE_NUMBER_PATTERN.fullmatch(detection):
        result.update(
            refined_kind="exhibit_or_file_identifier",
            candidate_kinds=["exhibit_or_file_identifier"],
            identifier_pattern_features=["sec_file_number"],
            reason_codes=["sec_file_number"],
        )
        return result

    duration_match = DURATION_PATTERN.fullmatch(detection)
    if duration_match:
        start = float(duration_match.group("start"))
        end = duration_match.group("end")
        result.update(
            refined_kind="duration",
            candidate_kinds=["duration"],
            numeric_value=start if end is None else None,
            range_start=start if end is not None else None,
            range_end=float(end) if end is not None else None,
            reason_codes=["duration_range" if end is not None else "duration"],
        )
        return result

    suffix_match = FOOTNOTE_SUFFIX_PATTERN.search(detection)
    candidate_without_suffix = detection
    if (
        suffix_match
        and suffix_match.start() > 0
        and "/" not in detection[: suffix_match.start()]
    ):
        candidate_without_suffix = detection[: suffix_match.start()].rstrip()
        result["footnote_suffix"] = suffix_match.group("suffix")

    plus_minus_match = re.fullmatch(
        rf"\+/-\s*(?P<value>[$€£¥]?\s*{NUMERIC_CORE})(?:\s*(?P<bps>bps?))?",
        candidate_without_suffix,
        re.IGNORECASE,
    )
    if plus_minus_match:
        details = _numeric_scalar_details(plus_minus_match.group("value"))
        endpoint = abs(details["numeric_value"])
        result.update(
            refined_kind="numeric_range",
            candidate_kinds=["numeric_range"],
            range_start=-endpoint,
            range_end=endpoint,
            currency_marker=details["currency_marker"],
            currency_code=details["currency_code"],
            scale="basis_points" if plus_minus_match.group("bps") else None,
            reason_codes=["symmetric_sensitivity_range"],
        )
        return result

    paired_match = re.fullmatch(r"(.+?)/(.+)", candidate_without_suffix)
    if paired_match:
        left_details = _numeric_scalar_details(paired_match.group(1).strip())
        right_details = _numeric_scalar_details(paired_match.group(2).strip())
        if left_details and right_details:
            marker = (
                left_details["currency_marker"]
                or right_details["currency_marker"]
            )
            result.update(
                refined_kind="numeric_range",
                candidate_kinds=["numeric_range"],
                range_start=left_details["numeric_value"],
                range_end=right_details["numeric_value"],
                currency_marker=marker,
                currency_code=CURRENCY_MARKERS.get(marker),
                reason_codes=["paired_sensitivity_values"],
            )
            return result

    if EXHIBIT_IDENTIFIER_PATTERN.fullmatch(detection):
        result["candidate_kinds"].append("exhibit_or_file_identifier")
        result["identifier_pattern_features"].append("dotted_identifier")

    scalar = _numeric_scalar_details(candidate_without_suffix)
    if scalar:
        result.update(scalar)
        if scalar["percent"]:
            kind = "percentage"
        elif TABLE_YEAR_PATTERN.fullmatch(candidate_without_suffix):
            kind = "year"
        else:
            kind = "numeric_scalar"
        result["candidate_kinds"].insert(0, kind)
        if PAGE_IDENTIFIER_PATTERN.fullmatch(detection):
            result["candidate_kinds"].append("exhibit_or_file_identifier")
            result["identifier_pattern_features"].append(
                "page_or_short_identifier"
            )
        if FOOTNOTE_ONLY_PATTERN.fullmatch(display):
            result["candidate_kinds"].append("footnote_marker")
        result["refined_kind"] = kind
        result["reason_codes"].append("accounting_scalar" if scalar["accounting_negative"] else "numeric_scalar")
        return result

    range_parts = _split_typed_range(candidate_without_suffix)
    if range_parts:
        left, right = range_parts
        left_details = _numeric_scalar_details(left)
        right_details = _numeric_scalar_details(right)
        if left_details and right_details:
            is_percent = left_details["percent"] or right_details["percent"]
            left_year = TABLE_YEAR_PATTERN.fullmatch(left.lstrip("$€£¥ "))
            right_year = TABLE_YEAR_PATTERN.fullmatch(right.lstrip("$€£¥ "))
            kind = (
                "percentage_range"
                if is_percent
                else "year_range"
                if left_year and right_year
                else "numeric_range"
            )
            marker = left_details["currency_marker"] or right_details["currency_marker"]
            result.update(
                refined_kind=kind,
                range_start=left_details["numeric_value"],
                range_end=right_details["numeric_value"],
                currency_marker=marker,
                currency_code=CURRENCY_MARKERS.get(marker),
                percent=is_percent,
                candidate_kinds=[kind],
                reason_codes=["typed_range"],
            )
            return result

    if PAGE_IDENTIFIER_PATTERN.fullmatch(detection):
        result["candidate_kinds"].append("exhibit_or_file_identifier")
        result["identifier_pattern_features"].append("page_or_short_identifier")
    result["candidate_kinds"].append("text")
    result["reason_codes"].append("text_fallback")
    return result


def refine_cell_kind(
    analysis: dict,
    *,
    row_profile: dict | None = None,
    column_profile: dict | None = None,
    header_tokens: str = "",
) -> dict:
    """Resolve lexical ambiguity using only row/column grammar already available."""
    refined = dict(analysis)
    header = normalize_text(header_tokens).casefold()
    candidates = set(refined["candidate_kinds"])
    identifier_header = any(token in header for token in ("exhibit", "file number", "page"))
    stable_identifier = bool((column_profile or {}).get("stable_identifier_prefix"))
    if "exhibit_or_file_identifier" in candidates and (identifier_header or stable_identifier):
        refined["refined_kind"] = "exhibit_or_file_identifier"
        refined["confidence"] = 1.0
        refined["reason_codes"] = [*refined["reason_codes"], "identifier_column_grammar"]
    return refined


def is_numeric_table_value(value: str) -> bool:
    """Compatibility wrapper backed by typed analysis."""
    return analyze_cell_lexically(value)["refined_kind"] in NUMERIC_KINDS


def _origin_rows(structure: dict) -> dict[int, list[dict]]:
    rows: dict[int, list[dict]] = defaultdict(list)
    for cell in structure["raw_cells"]:
        if cell["text"]:
            rows[cell["row"]].append(cell)
    for cells in rows.values():
        cells.sort(key=lambda cell: cell["physical_start"])
    return dict(rows)


def identify_promoted_rows(structure: dict) -> dict:
    """Identify leading internal titles and unit-only rows before role scoring."""
    rows = _origin_rows(structure)
    source_width = max((cell["physical_end"] for cell in structure["raw_cells"]), default=0)
    title_rows: list[int] = []
    unit_rows: list[int] = []
    for row_index in sorted(rows)[:6]:
        cells = rows[row_index]
        values = [cell["text"] for cell in cells]
        if values and all(UNIT_ONLY_PATTERN.fullmatch(value.strip()) for value in values):
            unit_rows.append(row_index)
            continue
        if len(cells) == 1:
            cell = cells[0]
            coverage = (cell["physical_end"] - cell["physical_start"]) / max(source_width, 1)
            text = cell["text"]
            if (
                coverage >= 0.70
                and not TABLE_PERIOD_PATTERN.search(text)
                and not re.search(r"\b(?:year|years|months|quarters?)\s+ended\b", text, re.I)
                and not TABLE_UNITS_PATTERN.fullmatch(text)
                and not is_numeric_table_value(text)
                and len(text) <= 250
            ):
                title_rows.append(row_index)
    return {"internal_title_rows": title_rows[:1], "unit_rows": unit_rows}


def _signature_kind(kind: str) -> str:
    if kind in {"currency_marker", "percent_marker", "footnote_marker"}:
        return "affix"
    if kind in NUMERIC_KINDS:
        return "value"
    return "text"


def _signature_similarity(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    size = max(len(left), len(right))
    padded_left = left + [""] * (size - len(left))
    padded_right = right + [""] * (size - len(right))
    return sum(a == b for a, b in zip(padded_left, padded_right)) / size


def build_row_profiles(structure: dict, *, excluded_rows: set[int] | None = None) -> list[dict]:
    rows = _origin_rows(structure)
    excluded_rows = excluded_rows or set()
    profiles = []
    for row_index in sorted(rows):
        if row_index in excluded_rows:
            continue
        cells = rows[row_index]
        analyses = [analyze_cell_lexically(cell["text"]) for cell in cells]
        nonempty_count = len(cells)
        kinds = [analysis["refined_kind"] for analysis in analyses]
        signature = [_signature_kind(kind) for kind in kinds]
        period_count = sum(
            kind == "year" or bool(TABLE_PERIOD_PATTERN.search(cell["text"]))
            for cell, kind in zip(cells, kinds)
        )
        unit_count = sum(
            bool(TABLE_UNITS_PATTERN.search(cell["text"]))
            or any(token in cell["text"].casefold() for token in ("amount", "percent", "rate", "shares", "volume"))
            for cell in cells
        )
        nonyear_numeric = sum(kind in NUMERIC_KINDS - {"year", "year_range", "date"} for kind in kinds)
        first_kind = kinds[0] if kinds else "empty"
        later_values = sum(kind in NUMERIC_KINDS for kind in kinds[1:])
        profile = {
            "source_row_index": row_index,
            "cell_count": nonempty_count,
            "cell_ids": [cell["raw_cell_id"] for cell in cells],
            "typed_kinds": kinds,
            "signature": signature,
            "th_ratio": sum(cell["is_header"] for cell in cells) / nonempty_count,
            "bold_text_ratio": sum(cell["bold_text_ratio"] for cell in cells) / nonempty_count,
            "centered_ratio": sum(cell["alignment"] == "center" for cell in cells) / nonempty_count,
            "visible_bottom_border_ratio": sum(cell["has_bottom_border"] for cell in cells) / nonempty_count,
            "colspan_or_group_header_ratio": sum(
                cell["colspan"] > 1 and (cell["alignment"] == "center" or cell["bold_text_ratio"] >= 0.5)
                for cell in cells
            ) / nonempty_count,
            "period_or_year_group_ratio": period_count / nonempty_count,
            "unit_or_measure_vocabulary_ratio": unit_count / nonempty_count,
            "nonyear_numeric_currency_range_density": nonyear_numeric / nonempty_count,
            "label_followed_by_values_ratio": float(
                first_kind in {"text", "year", "date", "exhibit_or_file_identifier"}
                and later_values > 0
            ),
            "single_cell_wide": nonempty_count == 1,
            "repeated_body_signature_similarity": 0.0,
        }
        profiles.append(profile)

    for index, profile in enumerate(profiles):
        following = profiles[index + 1 : index + 4]
        profile["repeated_body_signature_similarity"] = max(
            (_signature_similarity(profile["signature"], other["signature"]) for other in following),
            default=0.0,
        )
        profile["header_score"] = (
            3.0 * profile["th_ratio"]
            + 2.0 * profile["bold_text_ratio"]
            + 1.0 * profile["centered_ratio"]
            + 1.0 * profile["visible_bottom_border_ratio"]
            + 1.5 * profile["colspan_or_group_header_ratio"]
            + 2.0 * profile["period_or_year_group_ratio"]
            + 1.0 * profile["unit_or_measure_vocabulary_ratio"]
            - 3.0 * profile["repeated_body_signature_similarity"]
            - 2.0 * profile["nonyear_numeric_currency_range_density"]
            - 1.0 * profile["label_followed_by_values_ratio"]
        )
    return profiles


def detect_table_header_rows(
    structure: dict,
    row_profiles: list[dict] | None = None,
    *,
    excluded_rows: set[int] | None = None,
) -> dict:
    """Choose a bounded contiguous source-row header prefix with diagnostics."""
    row_profiles = row_profiles or build_row_profiles(structure, excluded_rows=excluded_rows)
    scan = row_profiles[: TABLE_HEURISTICS["header_scan_max_nonempty_rows"]]
    explicit_prefix = 0
    for profile in scan:
        if profile["th_ratio"] < 0.5:
            break
        explicit_prefix += 1
    if len(scan) < 2:
        boundary = 0
        objectives = [(0, -sum(row["header_score"] for row in scan))]
    else:
        objectives = []
        for boundary_candidate in range(len(scan)):
            before = sum(row["header_score"] for row in scan[:boundary_candidate])
            after = sum(row["header_score"] for row in scan[boundary_candidate:])
            objectives.append((boundary_candidate, before - after))
        baseline = dict(objectives)[0]
        eligible = [
            candidate
            for candidate in objectives[1:]
            if candidate[1] - baseline
            >= TABLE_HEURISTICS["header_min_objective_margin_over_headerless"]
        ]
        if not eligible:
            boundary = 0
        else:
            best_value = max(value for _, value in eligible)
            near_best = [
                candidate
                for candidate, value in eligible
                if best_value - value <= TABLE_HEURISTICS["header_tie_epsilon"]
            ]
            boundary = min(near_best)
            while (
                boundary > 1
                and scan[boundary - 1]["single_cell_wide"]
                and scan[boundary - 1]["period_or_year_group_ratio"] == 0
                and scan[boundary - 1]["unit_or_measure_vocabulary_ratio"] == 0
            ):
                boundary -= 1
            # Never consume a body-shaped row without overwhelming header evidence.
            while boundary and (
                scan[boundary - 1]["label_followed_by_values_ratio"]
                and scan[boundary - 1]["repeated_body_signature_similarity"] >= 0.5
                and scan[boundary - 1]["bold_text_ratio"] < 0.5
                and scan[boundary - 1]["th_ratio"] < 0.5
            ):
                boundary -= 1

    # A leading row made predominantly from semantic ``th`` cells is explicit
    # source evidence, even when its value grammar resembles the body (years
    # above numeric values are the common SEC case).  The objective remains
    # responsible for extending the header beyond the explicit prefix.
    boundary = max(boundary, explicit_prefix)

    selected = scan[:boundary]
    source_indexes = [profile["source_row_index"] for profile in selected]
    mode = "headerless"
    if selected:
        mode = "explicit" if any(profile["th_ratio"] >= 0.5 for profile in selected) else "inferred"
    sorted_objectives = sorted(objectives, key=lambda pair: (-pair[1], pair[0]))
    best_objective = dict(objectives).get(boundary, objectives[0][1] if objectives else 0.0)
    runner_up = next((item for item in sorted_objectives if item[0] != boundary), None)
    margin = best_objective - dict(objectives).get(0, best_objective)
    reasons = []
    if mode == "headerless":
        reasons.append("objective_did_not_beat_headerless")
    else:
        if any(profile["th_ratio"] for profile in selected):
            reasons.append("explicit_th_cells")
        if any(profile["bold_text_ratio"] >= 0.5 for profile in selected):
            reasons.append("bold_header_evidence")
        if any(profile["period_or_year_group_ratio"] for profile in selected):
            reasons.append("period_group_evidence")
    display_indexes = [
        structure["physical_source_row_indexes"].index(index)
        for index in source_indexes
        if index in structure["physical_source_row_indexes"]
    ]
    return {
        "header_mode": mode,
        "header_confidence": min(1.0, max(0.0, margin / 6.0)) if selected else 1.0,
        "header_reasons": reasons,
        "header_row_source_indexes": source_indexes,
        "header_row_indexes": display_indexes,
        "selected_boundary": boundary,
        "runner_up_boundary": runner_up[0] if runner_up else None,
        "objective": best_objective,
        "objective_margin_over_headerless": margin,
        "row_role_diagnostics": [
            {
                "source_row_index": profile["source_row_index"],
                "header_score": round(profile["header_score"], 6),
                "features": {
                    key: profile[key]
                    for key in (
                        "th_ratio",
                        "bold_text_ratio",
                        "centered_ratio",
                        "visible_bottom_border_ratio",
                        "colspan_or_group_header_ratio",
                        "period_or_year_group_ratio",
                        "unit_or_measure_vocabulary_ratio",
                        "repeated_body_signature_similarity",
                        "nonyear_numeric_currency_range_density",
                        "label_followed_by_values_ratio",
                    )
                },
                "selected_as_header": profile["source_row_index"] in source_indexes,
                "reason_codes": ["header_prefix"] if profile["source_row_index"] in source_indexes else ["body_suffix"],
            }
            for profile in row_profiles
        ],
    }


def _atom_kind_role(kind: str) -> str:
    if kind in {"percentage", "percentage_range", "percent_marker"}:
        return "percent"
    if kind in {"date"}:
        return "date"
    if kind in {"year", "year_range", "duration"}:
        return "years"
    if kind in NUMERIC_KINDS or kind == "currency_marker":
        return "value"
    return "text"


def _merge_atom_text(left: str, right: str, mode: str) -> str:
    if mode in {"currency", "percent", "footnote", "parenthesis"}:
        return f"{left}{right}"
    return normalize_text(f"{left} {right}")


def _form_row_atoms(cells: list[dict]) -> list[dict]:
    atoms = []
    index = 0
    while index < len(cells):
        cell = cells[index]
        analysis = analyze_cell_lexically(cell["text"])
        atom_cells = [cell]
        text = cell["text"]
        end = cell["physical_end"]
        mode = None
        if index + 1 < len(cells):
            following = cells[index + 1]
            following_analysis = analyze_cell_lexically(following["text"])
            gap = following["physical_start"] - end
            if gap <= 1 and analysis["refined_kind"] == "currency_marker" and following_analysis["refined_kind"] in NUMERIC_KINDS:
                mode = "currency"
            elif gap <= 1 and analysis["refined_kind"] in NUMERIC_KINDS and following_analysis["refined_kind"] == "percent_marker":
                mode = "percent"
            elif (
                gap <= 1
                and analysis["refined_kind"] in NUMERIC_KINDS
                and FOOTNOTE_ONLY_PATTERN.fullmatch(following["text"])
                and (
                    re.fullmatch(r"[*†‡]+", following["text"])
                    or (
                        gap == 0
                        and not (
                            index + 2 < len(cells)
                            and analyze_cell_lexically(cells[index + 2]["text"])["refined_kind"]
                            == "percent_marker"
                        )
                    )
                )
            ):
                mode = "footnote"
            if mode:
                atom_cells.append(following)
                text = _merge_atom_text(text, following["text"], mode)
                end = following["physical_end"]
                index += 1
                analysis = analyze_cell_lexically(text)
        atoms.append(
            {
                "text": text,
                "physical_start": cell["physical_start"],
                "physical_end": end,
                "source_raw_cell_ids": [value["raw_cell_id"] for value in atom_cells],
                "source_cells": atom_cells,
                "analysis": analysis,
                "kind": analysis["refined_kind"],
                "typed_role": _atom_kind_role(analysis["refined_kind"]),
            }
        )
        index += 1
    return atoms


def _interval_overlap(left: tuple[int, int], right: tuple[int, int]) -> float:
    intersection = max(0, min(left[1], right[1]) - max(left[0], right[0]))
    return intersection / max(1, min(left[1] - left[0], right[1] - right[0]))


def _lane_interval(lane: dict) -> tuple[int, int]:
    if lane["role"] == "row_label":
        leftmost = min(interval[0] for interval in lane["physical_intervals"])
        candidates = [
            interval
            for interval in lane["physical_intervals"]
            if interval[0] == leftmost
        ]
        return tuple(min(candidates, key=lambda interval: (interval[1] - interval[0], interval[1])))
    starts = [interval[0] for interval in lane["physical_intervals"]]
    ends = [interval[1] for interval in lane["physical_intervals"]]
    return round(statistics.median(starts)), round(statistics.median(ends))


def _roles_compatible(atom: dict, lane: dict) -> bool:
    if atom.get("is_row_label"):
        return lane["role"] == "row_label"
    if lane["role"] == "row_label":
        return False
    if atom["typed_role"] == "percent" or lane["role"] == "percent":
        return {atom["typed_role"], lane["role"]} <= {"percent", "value"}
    return True


def _best_lane(atom: dict, lanes: list[dict], used: set[int]) -> int | None:
    if atom.get("is_row_label"):
        return next(
            (
                index
                for index, lane in enumerate(lanes)
                if index not in used and lane["role"] == "row_label"
            ),
            None,
        )
    interval = (atom["physical_start"], atom["physical_end"])
    candidates = []
    for lane_index, lane in enumerate(lanes):
        if lane_index in used or lane["role"] == "row_label":
            continue
        anchor = _lane_interval(lane)
        overlap = _interval_overlap(interval, anchor)
        if overlap < TABLE_HEURISTICS["lane_interval_overlap_coefficient"]:
            continue
        role_penalty = 0 if _roles_compatible(atom, lane) else 1
        candidates.append(
            (
                -overlap,
                role_penalty,
                abs((interval[0] + interval[1]) / 2 - (anchor[0] + anchor[1]) / 2),
                abs(interval[0] - anchor[0]),
                lane_index,
            )
        )
    return min(candidates)[-1] if candidates else None


def _row_role(atoms: list[dict]) -> str:
    if not atoms:
        return "footnote"
    label = atoms[0]["text"].strip().casefold()
    if len(atoms) == 1 and atoms[0]["typed_role"] == "text":
        if label.startswith(("note:", "notes:", "(a)", "(b)", "*")):
            return "footnote"
        return "section_label"
    if label.startswith("total") or label in {"net", "ending balance"}:
        return "total"
    if "subtotal" in label:
        return "subtotal"
    return "data"


def normalize_logical_columns(
    structure: dict,
    header_detection: dict,
    row_profiles: list[dict] | None = None,
    *,
    promoted_rows: dict | None = None,
) -> dict:
    """Normalize SEC presentation intervals into stable semantic logical lanes."""
    promoted_rows = promoted_rows or identify_promoted_rows(structure)
    excluded = set(header_detection["header_row_source_indexes"])
    excluded.update(promoted_rows["internal_title_rows"])
    excluded.update(promoted_rows["unit_rows"])
    source_rows = _origin_rows(structure)
    body_atoms: dict[int, list[dict]] = {}
    for row_index, cells in source_rows.items():
        if row_index not in excluded:
            atoms = _form_row_atoms(cells)
            if atoms:
                if len(atoms) >= 2:
                    atoms[0]["is_row_label"] = True
                elif atoms[0]["typed_role"] == "text":
                    atoms[0]["is_row_label"] = True
                body_atoms[row_index] = atoms

    if not body_atoms:
        raise ValueError("Table normalization found no logical body rows")

    anchor_row_index, anchor_atoms = max(
        body_atoms.items(),
        key=lambda item: (len(item[1]), -item[0]),
    )
    lanes = []
    for atom in anchor_atoms:
        role = "row_label" if atom.get("is_row_label") else atom["typed_role"]
        lanes.append(
            {
                "physical_intervals": [[atom["physical_start"], atom["physical_end"]]],
                "role": role,
                "support_rows": {anchor_row_index},
                "fallback_reasons": [],
            }
        )

    fallback_reasons = []
    for row_index, atoms in body_atoms.items():
        if row_index == anchor_row_index:
            continue
        used: set[int] = set()
        for atom in atoms:
            lane_index = _best_lane(atom, lanes, used)
            if lane_index is None:
                role = "row_label" if atom.get("is_row_label") else atom["typed_role"]
                lanes.append(
                    {
                        "physical_intervals": [[atom["physical_start"], atom["physical_end"]]],
                        "role": role,
                        "support_rows": {row_index},
                        "fallback_reasons": ["conservative_separate_lane"],
                    }
                )
                lane_index = len(lanes) - 1
                fallback_reasons.append("conservative_separate_lane")
            else:
                interval = [atom["physical_start"], atom["physical_end"]]
                if interval not in lanes[lane_index]["physical_intervals"]:
                    lanes[lane_index]["physical_intervals"].append(interval)
                lanes[lane_index]["support_rows"].add(row_index)
                if atom["typed_role"] == "percent" and lanes[lane_index]["role"] == "value":
                    lanes[lane_index]["role"] = "percent"
            used.add(lane_index)

    lanes.sort(key=lambda lane: (_lane_interval(lane)[0], _lane_interval(lane)[1], lane["role"] != "row_label"))
    row_label_indexes = [index for index, lane in enumerate(lanes) if lane["role"] == "row_label"]
    if len(row_label_indexes) > 1:
        primary = row_label_indexes[0]
        for extra in reversed(row_label_indexes[1:]):
            # A repeated wide label interval is the same semantic lane when it never
            # co-occurs with another row-label atom.
            lanes[primary]["physical_intervals"].extend(
                interval for interval in lanes[extra]["physical_intervals"]
                if interval not in lanes[primary]["physical_intervals"]
            )
            lanes[primary]["support_rows"].update(lanes[extra]["support_rows"])
            lanes.pop(extra)

    logical_rows = []
    logical_cell_sources = []
    logical_cell_states = []
    logical_row_source_indexes = []
    logical_row_roles = []
    unresolved = []
    collisions = []
    atom_lane_by_raw_id = {}
    for row_index in sorted(body_atoms):
        atoms = body_atoms[row_index]
        role = _row_role(atoms)
        values = [""] * len(lanes)
        sources = [[] for _ in lanes]
        states = ["not_applicable" if role in {"section_label", "footnote"} else "missing_blank_in_aligned_lane" for _ in lanes]
        used: set[int] = set()
        for atom in atoms:
            lane_index = _best_lane(atom, lanes, used)
            if lane_index is None:
                unresolved.extend(atom["source_raw_cell_ids"])
                continue
            if values[lane_index]:
                collisions.append(
                    {
                        "source_row_index": row_index,
                        "logical_column": lane_index,
                        "raw_cell_ids": atom["source_raw_cell_ids"],
                    }
                )
                continue
            values[lane_index] = atom["text"]
            sources[lane_index] = atom["source_raw_cell_ids"]
            states[lane_index] = (
                "missing_marker"
                if atom["analysis"]["missing_value"]
                or atom["text"].lstrip("$€£¥").rstrip("%").strip() in MISSING_MARKERS
                else "present"
            )
            for raw_id in atom["source_raw_cell_ids"]:
                atom_lane_by_raw_id[raw_id] = (len(logical_rows), lane_index)
            used.add(lane_index)
        logical_rows.append(values)
        logical_cell_sources.append(sources)
        logical_cell_states.append(states)
        logical_row_source_indexes.append(row_index)
        logical_row_roles.append(role)

    body_rowspans = []
    cell_by_id = {cell["raw_cell_id"]: cell for cell in structure["raw_cells"]}
    source_to_logical_row = {source: index for index, source in enumerate(logical_row_source_indexes)}
    for raw_id, (logical_row, logical_column) in atom_lane_by_raw_id.items():
        cell = cell_by_id[raw_id]
        if cell["rowspan"] <= 1:
            continue
        covered_source_rows = range(cell["row"] + 1, cell["row"] + cell["rowspan"])
        covered = [source_to_logical_row[row] for row in covered_source_rows if row in source_to_logical_row]
        if covered:
            end = max(covered) + 1
            body_rowspans.append(
                {
                    "source_raw_cell_id": raw_id,
                    "logical_column": logical_column,
                    "logical_row_start": logical_row,
                    "logical_row_end": end,
                }
            )
            for covered_row in covered:
                if not logical_rows[covered_row][logical_column]:
                    logical_cell_states[covered_row][logical_column] = "rowspan_covered"

    logical_columns = []
    eligible_lane_rows = sum(len(atoms) >= 2 for atoms in body_atoms.values())
    for lane in lanes:
        support = len(lane["support_rows"])
        if (
            support >= TABLE_HEURISTICS["lane_min_support_rows"]
            and support / max(eligible_lane_rows, 1)
            >= TABLE_HEURISTICS["lane_min_support_fraction"]
        ):
            lane["fallback_reasons"] = []
    active_fallback_reasons = sorted(
        {
            reason
            for lane in lanes
            for reason in lane["fallback_reasons"]
        }
    )
    if len(body_atoms) == 1:
        active_fallback_reasons.append("one_row_leaf_header_support")
    for index, lane in enumerate(lanes):
        logical_columns.append(
            {
                "logical_index": index,
                "physical_intervals": sorted(lane["physical_intervals"]),
                "role": lane["role"],
                "unit": "unknown",
            }
        )

    source_coordinate_width = max((cell["physical_end"] for cell in structure["raw_cells"]), default=0)
    physical_display_width = len(structure["physical_source_column_indexes"])
    nonempty_source_rows = sorted(source_rows)
    occupied_coordinates = sum(
        cell["physical_end"] - cell["physical_start"]
        for cell in structure["raw_cells"]
        if cell["text"] and cell["row"] in nonempty_source_rows
    )
    source_slots = len(nonempty_source_rows) * source_coordinate_width
    physical_slots = len(structure["physical_rows"]) * physical_display_width
    physical_nonempty = sum(bool(value) for row in structure["physical_rows"] for value in row)
    data_indexes = [
        index for index, role in enumerate(logical_row_roles)
        if role not in {"section_label", "footnote"}
    ]
    logical_slots = len(data_indexes) * len(lanes)
    logical_nonempty = sum(bool(logical_rows[index][column]) for index in data_indexes for column in range(len(lanes)))
    ignored = []
    for row_index in promoted_rows["unit_rows"]:
        for cell in source_rows.get(row_index, []):
            ignored.append(
                {
                    "raw_cell_id": cell["raw_cell_id"],
                    "reason_code": "promoted_unit_row",
                    "equivalent_raw_cell_id": None,
                    "promoted_to": "unit",
                    "note": None,
                }
            )
    for row_index in promoted_rows["internal_title_rows"]:
        for cell in source_rows.get(row_index, []):
            ignored.append(
                {
                    "raw_cell_id": cell["raw_cell_id"],
                    "reason_code": "promoted_internal_title",
                    "equivalent_raw_cell_id": None,
                    "promoted_to": "title",
                    "note": None,
                }
            )

    diagnostics = {
        "status": "ok" if not unresolved and not collisions else "failed",
        "source_coordinate_width": source_coordinate_width,
        "physical_display_width": physical_display_width,
        "logical_width": len(lanes),
        "source_coordinate_empty_density": 1 - occupied_coordinates / source_slots if source_slots else 0.0,
        "physical_display_empty_density": 1 - physical_nonempty / physical_slots if physical_slots else 0.0,
        "logical_empty_density": 1 - logical_nonempty / logical_slots if logical_slots else 0.0,
        "source_coordinate_to_logical_width_ratio": source_coordinate_width / len(lanes),
        "physical_display_to_logical_width_ratio": physical_display_width / len(lanes),
        "logical_to_physical_display_fraction": len(lanes) / physical_display_width if physical_display_width else 0.0,
        "unmapped_nonempty_raw_cell_ids": sorted(set(unresolved)),
        "ignored_raw_cells": ignored,
        "collisions": collisions,
        "fallback_used": bool(active_fallback_reasons),
        "fallback_reasons": sorted(set(active_fallback_reasons)),
        "anchor_source_row_index": anchor_row_index,
    }
    return {
        "normalization_mode": "semantic_grid",
        "logical_width": len(lanes),
        "logical_columns": logical_columns,
        "logical_rows": logical_rows,
        "logical_row_source_indexes": logical_row_source_indexes,
        "logical_row_roles": logical_row_roles,
        "logical_cell_sources": logical_cell_sources,
        "logical_cell_states": logical_cell_states,
        "logical_body_rowspans": body_rowspans,
        "normalization_diagnostics": diagnostics,
        "promoted_rows": promoted_rows,
    }


def project_logical_headers(
    structure: dict,
    logical_fragment: dict,
    header_detection: dict,
) -> dict:
    width = logical_fragment["logical_width"]
    lanes = logical_fragment["logical_columns"]
    rows_by_source = _origin_rows(structure)
    header_rows = []
    header_sources = [[[] for _ in range(width)] for _ in header_detection["header_row_source_indexes"]]
    unprojected = []
    unit_header_ids = []
    unit_header_evidence = [[] for _ in range(width)]
    for output_row, source_row in enumerate(header_detection["header_row_source_indexes"]):
        values = [""] * width
        for cell in rows_by_source.get(source_row, []):
            interval = (cell["physical_start"], cell["physical_end"])
            projected = []
            for lane_index, lane in enumerate(lanes):
                anchor = _lane_interval(lane)
                center = (anchor[0] + anchor[1]) / 2
                if interval[0] <= center < interval[1]:
                    projected.append(lane_index)
            if not projected:
                projected = [
                    lane_index
                    for lane_index, lane in enumerate(lanes)
                    if _interval_overlap(interval, _lane_interval(lane)) > 0
                ]
            if not projected:
                unprojected.append(cell["raw_cell_id"])
                continue
            if UNIT_ONLY_PATTERN.fullmatch(cell["text"].strip()):
                unit_header_ids.append(cell["raw_cell_id"])
                for lane_index in projected:
                    unit_header_evidence[lane_index].append(
                        {
                            "text": cell["text"],
                            "raw_cell_id": cell["raw_cell_id"],
                        }
                    )
                continue
            for lane_index in projected:
                if values[lane_index] and values[lane_index] != cell["text"]:
                    values[lane_index] = normalize_text(f"{values[lane_index]} {cell['text']}")
                else:
                    values[lane_index] = cell["text"]
                header_sources[output_row][lane_index].append(cell["raw_cell_id"])
        header_rows.append(values)

    paths = []
    metadata = []
    for column in range(width):
        labels = []
        source_ids = []
        for row_index, row in enumerate(header_rows):
            label = row[column]
            if label and (not labels or normalize_text(label).casefold() != normalize_text(labels[-1]).casefold()):
                labels.append(label)
            source_ids.extend(header_sources[row_index][column])
        paths.append(labels)
        metadata.append(
            {
                "source_raw_cell_ids": list(dict.fromkeys(source_ids)),
                "generated_components": [],
            }
        )
    return {
        "logical_header_rows": header_rows,
        "logical_header_paths": paths,
        "logical_column_header_metadata": metadata,
        "unprojected_header_raw_cell_ids": unprojected,
        "unit_header_raw_cell_ids": list(dict.fromkeys(unit_header_ids)),
        "logical_unit_header_evidence": unit_header_evidence,
    }


def _scale_from_text(text: str) -> str | None:
    lowered = text.casefold()
    for scale in ("thousands", "millions", "billions"):
        if re.search(rf"\bin\s+{scale}\b", lowered):
            return scale
    return None


def infer_logical_column_units(
    structure: dict,
    logical_fragment: dict,
    header_projection: dict,
    *,
    title: str | None = None,
    title_source_block_id: str | None = None,
    inherited_from_block_id: str | None = None,
) -> dict:
    width = logical_fragment["logical_width"]
    cell_lookup = {cell["raw_cell_id"]: cell for cell in structure["raw_cells"]}
    promoted_unit_ids = {
        cell["raw_cell_id"]
        for cell in structure["raw_cells"]
        if cell["row"] in logical_fragment["promoted_rows"]["unit_rows"] and cell["text"]
    }
    unit_evidence_ids = promoted_unit_ids | set(header_projection.get("unit_header_raw_cell_ids") or [])
    header_unit_ids = {
        cell["raw_cell_id"]
        for cell in structure["raw_cells"]
        if cell["row"] in {
            value
            for value in logical_fragment.get("promoted_rows", {}).get("unit_rows", [])
        }
        or TABLE_UNITS_PATTERN.search(cell["text"] or "")
    }
    unit_evidence_ids |= header_unit_ids
    promoted_unit_text = " ".join(cell_lookup[cell_id]["text"] for cell_id in unit_evidence_ids)
    global_scale = _scale_from_text(" ".join(value for value in (title or "", promoted_unit_text) if value))
    units = []
    metadata = []
    for column in range(width):
        lane = logical_fragment["logical_columns"][column]
        source_ids = [
            raw_id
            for row in logical_fragment["logical_cell_sources"]
            for raw_id in row[column]
        ]
        texts = [cell_lookup[raw_id]["text"] for raw_id in source_ids]
        kinds = [analyze_cell_lexically(text)["refined_kind"] for text in texts]
        header_text = " ".join(header_projection["logical_header_paths"][column])
        column_unit_evidence = header_projection.get("logical_unit_header_evidence", [[] for _ in range(width)])[column]
        column_unit_text = " ".join(value["text"] for value in column_unit_evidence)
        header_scale = _scale_from_text(header_text + " " + column_unit_text)
        scale = header_scale or global_scale
        currency_markers = {
            marker
            for text in texts
            for marker in CURRENCY_MARKERS
            if text.strip().startswith(marker) or text.strip() == marker
        }
        xbrl_units = {
            value.casefold()
            for raw_id in source_ids
            for value in cell_lookup[raw_id].get("xbrl_unit_refs", [])
        }
        xbrl_scales = {
            value
            for raw_id in source_ids
            for value in cell_lookup[raw_id].get("xbrl_scales", [])
        }
        xbrl_scale = (
            "billions"
            if "9" in xbrl_scales
            else "millions"
            if "6" in xbrl_scales
            else "thousands"
            if "3" in xbrl_scales
            else None
        )
        has_percent = any(
            kind in {"percentage", "percentage_range"} for kind in kinds
        ) or any(text.strip() == "%" for text in texts)
        has_date = any(kind == "date" for kind in kinds)
        has_year_or_duration = any(
            kind in {"year", "year_range", "duration"} for kind in kinds
        )
        has_currency = bool(currency_markers or xbrl_units & {"usd", "eur", "gbp", "jpy"})
        typed_unit_families = sum(
            (has_percent, has_currency, has_date or has_year_or_duration)
        )
        if lane["role"] == "row_label":
            unit = "text"
            source_kind = "column_role"
            reason = "row_label_lane"
            evidence_ids = []
        elif typed_unit_families > 1:
            unit = "mixed"
            source_kind = "body_values"
            reason = "heterogeneous_typed_values"
            evidence_ids = source_ids
        elif has_percent or re.search(r"\b(?:percent|effective rate)\b", header_text, re.I):
            unit = "percent"
            has_percent_marker = any(text.strip() == "%" for text in texts)
            if has_percent_marker:
                source_kind = "body_markers"
                reason = "explicit_percent_marker"
            elif has_percent:
                source_kind = "body_values"
                reason = "percentage_values"
            else:
                source_kind = "header"
                reason = "percent_header"
            evidence_ids = source_ids
        elif has_currency:
            currency = (
                "usd"
                if "$" in currency_markers or "usd" in xbrl_units
                else "eur"
                if "€" in currency_markers or "eur" in xbrl_units
                else "gbp"
                if "£" in currency_markers or "gbp" in xbrl_units
                else "jpy"
            )
            effective_scale = header_scale or xbrl_scale or global_scale
            unit = f"{currency}_{effective_scale}" if effective_scale else currency
            source_kind = "inline_xbrl" if xbrl_units else "body_markers"
            reason = (
                f"inline_xbrl_{effective_scale or 'currency'}"
                if xbrl_units
                else "explicit_currency_marker"
                if not effective_scale
                else f"currency_marker_{effective_scale}_scale"
            )
            evidence_ids = source_ids or [value["raw_cell_id"] for value in column_unit_evidence]
        elif re.search(r"\b(?:shares?|vehicles?)\b", header_text + " " + (title or ""), re.I):
            measure = "vehicles" if re.search(r"\bvehicles?\b", header_text + " " + (title or ""), re.I) else "shares"
            unit = f"{measure}_{scale}" if scale else measure
            source_kind = "header" if header_text else "title_scale"
            reason = f"{measure}_measure"
            evidence_ids = header_projection["logical_column_header_metadata"][column]["source_raw_cell_ids"]
        elif any(kind == "date" for kind in kinds):
            unit, source_kind, reason, evidence_ids = "date", "body_values", "date_values", source_ids
        elif any(kind in {"year", "year_range", "duration"} for kind in kinds):
            unit, source_kind, reason, evidence_ids = "years", "body_values", "year_values", source_ids
        elif any(kind in {"numeric_scalar", "numeric_range", "missing_numeric"} for kind in kinds):
            unit = f"number_{scale}" if scale else "number"
            source_kind = "body_values"
            reason = "numeric_values" if not scale else f"numeric_{scale}_scale"
            evidence_ids = source_ids
        else:
            unit, source_kind, reason, evidence_ids = "text", "body_values", "text_values", source_ids
        lane["unit"] = unit
        units.append(unit)
        metadata.append(
            {
                "source_kind": source_kind,
                "source_block_ids": [title_source_block_id] if source_kind == "title_scale" and title_source_block_id else [],
                "source_raw_cell_ids": list(
                    dict.fromkeys(
                        (evidence_ids or unit_evidence_ids) if scale else evidence_ids
                    )
                ),
                "inherited_from_block_id": inherited_from_block_id,
                "confidence": 1.0 if unit != "unknown" else 0.0,
                "reason_code": reason,
            }
        )
    nontext = {unit for unit in units if unit not in {"text", "number", "unknown"}}
    if len(nontext) == 1:
        summary = next(iter(nontext))
    elif len(nontext) > 1:
        summary = "mixed"
    else:
        summary = None
    return {
        "logical_column_units": units,
        "logical_column_unit_metadata": metadata,
        "units": summary,
    }


def finalize_logical_headers(
    logical_fragment: dict,
    header_projection: dict,
    logical_units: list[str],
) -> dict:
    paths = header_projection["logical_header_paths"]
    metadata = header_projection["logical_column_header_metadata"]
    width = logical_fragment["logical_width"]
    header_context = []
    header_context_source_ids = []
    value_indexes = [index for index, lane in enumerate(logical_fragment["logical_columns"]) if lane["role"] != "row_label" and paths[index]]
    if len(value_indexes) >= 2:
        minimum = min(len(paths[index]) for index in value_indexes)
        prefix_length = 0
        for position in range(minimum):
            labels = {normalize_text(paths[index][position]).casefold() for index in value_indexes}
            if len(labels) == 1 and all(len(paths[index]) > position + 1 for index in value_indexes):
                prefix_length += 1
            else:
                break
        if prefix_length:
            header_context = paths[value_indexes[0]][:prefix_length]
            context_labels = {normalize_text(value).casefold() for value in header_context}
            for index in value_indexes:
                for raw_id in metadata[index]["source_raw_cell_ids"]:
                    # Source IDs can cover parent and leaf; include parent IDs once.
                    if raw_id not in header_context_source_ids:
                        header_context_source_ids.append(raw_id)
                paths[index] = paths[index][prefix_length:]

    headers = []
    for index in range(width):
        path = paths[index]
        header = " — ".join(path)
        if not header and logical_fragment["logical_columns"][index]["role"] == "row_label" and header_projection["logical_header_rows"]:
            header = "Line item"
            metadata[index]["generated_components"].append("Line item")
        headers.append(header)
    groups = defaultdict(list)
    for index, header in enumerate(headers):
        if header:
            groups[normalize_text(header).casefold()].append(index)
    for indexes in groups.values():
        if len(indexes) <= 1:
            continue
        for index in indexes:
            unit = logical_units[index]
            if unit == "percent":
                component = "Percent"
            elif unit in {"years", "date"}:
                component = "Date" if unit == "date" else "Maturity"
            elif unit not in {"text", "unknown"}:
                component = "Amount"
            else:
                component = "Value"
            headers[index] = f"{headers[index]} — {component}"
            metadata[index]["generated_components"].append(component)
    return {
        "logical_header_rows": header_projection["logical_header_rows"],
        "logical_header_paths": paths,
        "logical_header_context": header_context,
        "logical_header_context_source_raw_cell_ids": header_context_source_ids,
        "logical_column_headers": headers,
        "logical_column_header_metadata": metadata,
    }


def build_column_headers(structure: dict, header_rows: list[int] | dict) -> list[str]:
    """Compatibility helper for deprecated physical header fields."""
    if isinstance(header_rows, dict):
        header_rows = header_rows["header_row_indexes"]
    if not structure["physical_expanded_rows"]:
        return []
    headers = []
    for column in range(len(structure["physical_expanded_rows"][0])):
        labels = []
        for row_index in header_rows:
            label = structure["physical_expanded_rows"][row_index][column]
            if label and label not in labels:
                labels.append(label)
        headers.append(" — ".join(labels))
    return headers


def extract_column_units(rows: list[list[str]]) -> list[str | None]:
    """Deprecated physical-grid unit helper retained for transition diagnostics."""
    width = max((len(row) for row in rows), default=0)
    result = [None] * width
    for row in rows:
        for index, value in enumerate(row):
            analysis = analyze_cell_lexically(value)
            if analysis["refined_kind"] in {"percentage", "percentage_range", "percent_marker"}:
                result[index] = "percent"
            elif analysis["currency_marker"]:
                result[index] = "dollars" if analysis["currency_marker"] == "$" else "currency"
    return result


def extract_table_units(rows: list[list[str]], column_units: list[str | None] | None = None) -> str | None:
    text = " ".join(value for row in rows[:6] for value in row if value)
    match = TABLE_UNITS_PATTERN.search(text)
    units = {value for value in (column_units or extract_column_units(rows)) if value}
    if len(units) > 1:
        return "mixed"
    if match:
        return normalize_text(match.group(0))
    return next(iter(units), None)


def _caption_sentence(text: str) -> str | None:
    matches = list(TABLE_CAPTION_PATTERN.finditer(text))
    if not matches:
        return None
    cue_start = matches[-1].start()
    boundaries = list(re.finditer(r"[.!?]\s+", text[:cue_start]))
    start = boundaries[-1].end() if boundaries else 0
    following_boundary = re.search(r"[.!?](?:\s+|$)", text[matches[-1].end() :])
    end = (
        matches[-1].end() + following_boundary.start() + 1
        if following_boundary
        else len(text)
    )
    return normalize_text(text[start:end]).rstrip(":")


def _title_rejection_reason(text: str, company: str = "") -> str | None:
    value = normalize_text(text)
    if not value:
        return "empty"
    if company and value.casefold() in {company.casefold(), f"{company} and subsidiaries".casefold()}:
        return "company_header"
    if GENERIC_COMPANY_HEADER_PATTERN.fullmatch(value):
        return "company_header"
    if GENERIC_FINANCIAL_HEADER_PATTERN.fullmatch(value):
        return "generic_notes_header"
    if value.casefold() == "table of contents":
        return "table_of_contents"
    if PAGE_LABEL_PATTERN.fullmatch(value):
        return "page_label"
    if (
        UNIT_ONLY_PATTERN.fullmatch(value)
        or TABLE_PERIOD_PATTERN.fullmatch(value)
        or re.match(r"^\(?\s*in\s+(?:thousands|millions|billions)\b", value, re.I)
    ):
        return "unit_or_period_only"
    if len(value) > TABLE_HEURISTICS["title_caption_max_characters"]:
        return "overlong"
    if re.search(r"\b(?:and|or|of|to|for|with|by|in|on|at)$", value, re.I):
        return "incomplete"
    return None


def select_table_title(
    node,
    recent_blocks: list[dict],
    internal_title_cells: list[dict],
    *,
    company: str = "",
    document_region: str | None = None,
) -> dict:
    """Select bounded native title evidence and retain rejected candidates."""
    rejected = []
    caption_nodes = node.xpath("./caption[normalize-space()]")
    if caption_nodes:
        text = collect_visible_text(caption_nodes[0])
        reason = _title_rejection_reason(text, company)
        if not reason:
            return _title_result(text, "html_caption", None, [], f"xpath:{node.getroottree().getpath(caption_nodes[0])}", 1.0, "accepted_caption", rejected)
        rejected.append({"text": text, "source": "html_caption", "reason_codes": [reason]})

    for cell in internal_title_cells:
        text = cell["text"]
        reason = _title_rejection_reason(text, company)
        if not reason:
            return _title_result(text, "internal_title_row", None, [cell["raw_cell_id"]], f"raw-cell:{cell['raw_cell_id']}", 1.0, "accepted_internal", rejected)
        rejected.append({"text": text, "source": "internal_title_row", "reason_codes": [reason]})

    candidate_blocks = list(recent_blocks)
    if not candidate_blocks:
        preceding = node.xpath(
            "preceding::*[self::p or self::div or self::h1 or self::h2 or "
            "self::h3 or self::h4 or self::h5 or self::h6][normalize-space()]"
        )
        for candidate in reversed(preceding):
            if any(raw_tag(ancestor) == "table" for ancestor in candidate.iterancestors()):
                continue
            text = collect_visible_text(candidate)
            if not text:
                continue
            candidate_blocks.insert(
                0,
                {
                    "block_id": None,
                    "content_type": (
                        "heading"
                        if raw_tag(candidate).startswith("h")
                        or (len(text) <= 200 and has_heading_style(candidate))
                        else "paragraph"
                    ),
                    "text": text,
                    "source_locator": f"xpath:{node.getroottree().getpath(candidate)}",
                },
            )
            if len(candidate_blocks) >= TABLE_HEURISTICS["title_lookback_max_meaningful_blocks"]:
                break

    candidates = []
    shared_heading = None
    for distance, block in enumerate(reversed(candidate_blocks)):
        if block.get("content_type") in {"data_table", "text_table", "unknown_table", "navigation"}:
            source_block_id = block.get("title_source_block_id")
            if (
                block.get("title_source") in {"heading", "inherited"}
                and source_block_id
                and block.get("document_region") in {None, document_region}
            ):
                shared_heading = next(
                    (
                        candidate
                        for candidate in reversed(candidate_blocks)
                        if candidate.get("block_id") == source_block_id
                        and candidate.get("content_type") == "heading"
                    ),
                    None,
                )
            break
        if document_region and block.get("document_region") not in {None, document_region}:
            break
        if block.get("heading_kind") == "item":
            break
        text = block.get("text", "")
        if not text:
            continue
        candidates.append((distance, block, text))
        if len(candidates) >= TABLE_HEURISTICS["title_lookback_max_meaningful_blocks"]:
            break
    if shared_heading is not None and all(
        candidate.get("block_id") != shared_heading.get("block_id")
        for _, candidate, _ in candidates
    ):
        candidates.append(
            (
                len(candidates),
                shared_heading,
                shared_heading.get("text", ""),
            )
        )

    for _, block, text in candidates:
        caption = _caption_sentence(text)
        if not caption:
            continue
        reason = _title_rejection_reason(caption, company)
        if reason:
            rejected.append({"text": caption, "source": "prose_caption", "reason_codes": [reason]})
            continue
        locator = block.get("source_locator") or f"block:{block['block_id']}"
        return _title_result(caption, "prose_caption", block["block_id"], [], locator, 1.0, "accepted_caption", rejected[:5])

    for _, block, text in candidates:
        if block.get("content_type") != "heading":
            continue
        reason = _title_rejection_reason(text, company)
        if reason:
            rejected.append({"text": text, "source": "heading", "reason_codes": [reason]})
            continue
        if len(text) <= TABLE_HEURISTICS["title_heading_max_characters"]:
            locator = block.get("source_locator") or f"block:{block['block_id']}"
            return _title_result(text.rstrip(":"), "heading", block["block_id"], [], locator, 0.85, "accepted_heading", rejected[:5])

    for _, block, text in candidates:
        reason = _title_rejection_reason(text, company) or "prose_without_caption_cue"
        rejected.append({"text": text[:500], "source": block.get("content_type", "block"), "reason_codes": [reason]})
    return _title_result(None, "none", None, [], None, 0.0, "missing", rejected[:5])


def _title_result(title, source, block_id, raw_ids, locator, confidence, status, rejected):
    return {
        "title": title,
        "title_source": source,
        "title_source_block_id": block_id,
        "title_source_raw_cell_ids": raw_ids,
        "title_source_locator": locator,
        "title_confidence": confidence,
        "title_quality_status": status,
        "rejected_title_candidates": rejected,
    }


def find_table_title(node) -> str | None:
    """Compatibility title lookup for callers without extraction context."""
    candidates = node.xpath(
        "preceding::*[self::p or self::div or self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6][normalize-space()]"
    )
    recent = []
    for index, candidate in enumerate(reversed(candidates)):
        if any(raw_tag(ancestor) == "table" for ancestor in candidate.iterancestors()):
            continue
        text = collect_visible_text(candidate)
        recent.append(
            {
                "block_id": f"LOOKBACK-{index}",
                "content_type": "heading" if raw_tag(candidate).startswith("h") else "paragraph",
                "text": text,
            }
        )
        if len(recent) == TABLE_HEURISTICS["title_lookback_max_meaningful_blocks"]:
            break
    result = select_table_title(node, list(reversed(recent)), [], company="")
    return result["title"]


def _record_signature(row: list[str]) -> tuple[str, ...]:
    return tuple(_signature_kind(analyze_cell_lexically(value)["refined_kind"]) for value in row)


def classify_logical_table(
    node,
    structure: dict,
    logical: dict,
    *,
    section: str,
    document_region: str,
    title: str | None = None,
) -> dict:
    """Classify normalized content with named deterministic predicates."""
    rows = logical["logical_rows"]
    roles = logical["logical_row_roles"]
    records = [row for row, role in zip(rows, roles) if role not in {"section_label", "footnote"}]
    width = logical["logical_width"]
    headers = logical.get("logical_column_headers") or [""] * width
    values = [value for row in records for value in row if value]
    text = " ".join([title or "", *headers, *values])
    lowered = text.casefold()
    reasons = []
    scores = defaultdict(float)

    link_count = len(node.xpath(".//a[@href]")) if node is not None else 0
    item_refs = len(re.findall(r"\bitem\s+\d{1,2}[a-z]?\.?", text, re.I))
    last_values = [row[-1] for row in records if row and row[-1]]
    page_fraction = sum(bool(PAGE_IDENTIFIER_PATTERN.fullmatch(value.strip())) for value in last_values) / len(last_values) if last_values else 0.0
    first_values = [row[0] for row in records if row and row[0]]
    text_fraction = sum(any(character.isalpha() for character in value) for value in first_values) / len(first_values) if first_values else 0.0
    currency_count = sum(any(marker in value for marker in CURRENCY_MARKERS) for value in values)
    if (
        (len(records) >= TABLE_HEURISTICS["index_min_record_rows"] and width in {2, 3} and page_fraction >= TABLE_HEURISTICS["index_page_value_fraction"] and text_fraction >= TABLE_HEURISTICS["index_text_label_fraction"] and currency_count == 0)
        or "table of contents" in lowered
        or (link_count >= 3 and item_refs >= 2)
    ):
        reasons.append("index_page_column" if page_fraction else "index_item_links")
        scores["index_navigation"] = 1.0
        return _classification("index_navigation", reasons, scores)

    exhibit_header = bool(TABLE_EXHIBIT_PATTERN.search(" ".join(headers)))
    exhibit_records = sum(
        bool(EXHIBIT_RECORD_IDENTIFIER_PATTERN.fullmatch(row[0].strip()))
        and any(len(value.strip()) >= 10 for value in row[1:] if value)
        for row in records
        if row
    )
    exhibit_legend_records = sum(
        bool(re.fullmatch(r"[*†‡+#]+", row[0].strip()))
        and any(len(value.strip()) >= 10 for value in row[1:] if value)
        for row in records
        if row
    )
    if (
        exhibit_header
        or exhibit_records >= 2
        or (
            document_region == "exhibits"
            and (exhibit_records >= 1 or exhibit_legend_records >= 1)
        )
    ):
        reasons.append("exhibit_headers" if exhibit_header else "exhibit_record_grammar")
        scores["exhibit_list"] = 1.0
        return _classification("exhibit_list", reasons, scores)

    financial_families = []
    if document_region in {"financial_statements", "financial_statement_notes", "financial_statement_schedules"}:
        financial_families.append("financial_region")
    if TABLE_FINANCIAL_PATTERN.search(text):
        financial_families.append("financial_terms")
    typed = [analyze_cell_lexically(value) for value in values]
    if any(item["currency_marker"] or item["refined_kind"] in {"percentage", "percentage_range"} for item in typed):
        financial_families.append("currency_or_rate_values")
    if any(
        unit == "percent"
        or unit.startswith(("usd", "eur", "gbp", "jpy", "currency"))
        for unit in logical.get("logical_column_units", [])
    ):
        financial_families.append("scale_or_currency_units")
    if any(TABLE_PERIOD_PATTERN.search(header) for header in headers) or any(any(TABLE_YEAR_PATTERN.fullmatch(part) for part in path) for path in logical.get("logical_header_paths", [])):
        financial_families.append("period_headers")
    numeric_record_rows = sum(
        any(
            analyze_cell_lexically(value)["refined_kind"] in NUMERIC_KINDS
            or any(marker in value for marker in (*CURRENCY_MARKERS, "%"))
            for value in row[1:]
        )
        for row in records
    )
    if (len(records) >= 2 and numeric_record_rows >= 2 and len(set(financial_families)) >= 2) or (
        len(records) == 1 and width >= 3 and len(set(financial_families)) >= 3
    ):
        reason_map = {
            "financial_region": "financial_region",
            "financial_terms": "financial_terms",
            "currency_or_rate_values": "currency_values",
            "scale_or_currency_units": "scale_currency_evidence",
            "period_headers": "period_headers",
        }
        reasons.extend(reason_map[value] for value in dict.fromkeys(financial_families))
        reasons.append("repeated_record_grid" if len(records) >= 2 else "single_row_strong_financial_grid")
        scores["financial_data"] = min(1.0, len(set(financial_families)) / 4)
        return _classification("financial_data", reasons, scores)

    signature_values = [
        value.strip().casefold()
        for row in rows
        for value in row
        if value
    ]
    normalized_title = (title or "").strip().casefold()
    if document_region == "signatures" or normalized_title.startswith(
        ("/s/", "/s ", "/s\u00a0")
    ) or any(
        value.startswith("/s/")
        or value.startswith("/s ")
        or value.startswith("/s\u00a0")
        for value in signature_values
    ) or any(token in lowered for token in ("principal executive officer", "registrant")):
        reasons.append("signature_layout")
        scores["layout"] = 1.0
        return _classification("layout", reasons, scores)

    if width == 1:
        reasons.append("single_column_presentation_layout")
        scores["layout"] = 1.0
        return _classification("layout", reasons, scores)

    signatures = Counter(_record_signature(row) for row in records)
    signature_fraction = max(signatures.values(), default=0) / len(records) if records else 0.0
    meaningful_headers = sum(bool(value) for value in headers)
    typed_record_rows = sum(
        any(
            analyze_cell_lexically(value)["refined_kind"] in NUMERIC_KINDS
            for value in row[1:]
            if value
        )
        for row in records
    )
    headerless_leading_label_row = bool(
        logical.get("header_mode") == "headerless"
        and len(records) >= 3
        and all(
            value and analyze_cell_lexically(value)["refined_kind"] == "text"
            for value in records[0]
        )
        and all(
            any(
                analyze_cell_lexically(value)["refined_kind"] in NUMERIC_KINDS
                for value in row[1:]
                if value
            )
            for row in records[1:]
        )
    )
    if (
        len(records) >= TABLE_HEURISTICS["structured_min_record_rows"]
        and width >= 2
        and (
            signature_fraction
            >= TABLE_HEURISTICS["structured_signature_support_fraction"]
            or headerless_leading_label_row
        )
        and (
            meaningful_headers >= min(2, width)
            or bool(title)
            or typed_record_rows >= 2
        )
    ):
        reasons.append(
            "repeated_record_grid"
            if meaningful_headers
            else "headerless_repeated_record_grid"
        )
        if any(analyze_cell_lexically(value)["refined_kind"] == "date" for value in values):
            reasons.append("structured_name_date_grid")
        scores["structured_text"] = signature_fraction
        return _classification("structured_text", reasons, scores)

    if (
        width >= 2
        and meaningful_headers >= min(2, width)
        and records
        and (
            len(records) >= 2
            or bool(title)
        )
    ):
        reasons.append("headered_structured_records")
        scores["structured_text"] = max(0.5, signature_fraction)
        return _classification("structured_text", reasons, scores)

    if len(records) <= 2 and width <= 2 and any(token in lowered for token in ("signature", "by:", "name:", "title:")):
        reasons.append("signature_layout")
        scores["layout"] = 1.0
        return _classification("layout", reasons, scores)
    if len(records) == 1 and width <= 2 and records[0][0].strip().casefold() in {
        "note",
        "note:",
    }:
        reasons.append("single_note_layout")
        scores["layout"] = 1.0
        return _classification("layout", reasons, scores)
    if (
        width == 2
        and title
        and "critical audit matter" in title.casefold()
        and any(
            "how we addressed" in value.casefold()
            or "how we address" in value.casefold()
            for value in values
        )
    ):
        reasons.append("critical_audit_matter_layout")
        scores["layout"] = 1.0
        return _classification("layout", reasons, scores)
    reasons.append("low_confidence")
    scores["unknown"] = 1.0
    return _classification("unknown", reasons, scores)


def _classification(kind: str, reasons: list[str], scores) -> dict:
    coarse = {
        "financial_data": "data",
        "structured_text": "text",
        "index_navigation": "navigation",
        "exhibit_list": "text",
        "layout": "text",
        "unknown": "unknown",
    }[kind]
    content_type = {
        "data": "data_table",
        "text": "text_table",
        "navigation": "navigation",
        "unknown": "unknown_table",
    }[coarse]
    return {
        "table_kind": kind,
        "table_class": coarse,
        "content_type": content_type,
        "classification_reasons": reasons,
        "classification_scores": dict(scores),
    }


def classify_table(node, structure: dict, section: str) -> tuple[str, list[str]]:
    """Compatibility classifier that uses the logical pipeline."""
    promoted = identify_promoted_rows(structure)
    excluded = set(promoted["internal_title_rows"] + promoted["unit_rows"])
    profiles = build_row_profiles(structure, excluded_rows=excluded)
    header = detect_table_header_rows(structure, profiles, excluded_rows=excluded)
    logical = normalize_logical_columns(structure, header, profiles, promoted_rows=promoted)
    projection = project_logical_headers(structure, logical, header)
    unit_result = infer_logical_column_units(structure, logical, projection)
    logical.update(unit_result)
    logical.update(finalize_logical_headers(logical, projection, unit_result["logical_column_units"]))
    region = "financial_statements" if section.casefold().startswith("item 8") else "filing_body"
    result = classify_logical_table(node, structure, logical, section=section, document_region=region)
    return result["table_class"], result["classification_reasons"]


def _escape_markdown(value: object) -> str:
    return normalize_text(str(value or "")).replace("|", "\\|").replace("\n", "<br>")


def _display_headers(fragment: dict) -> list[str]:
    headers = list(fragment.get("logical_column_headers") or [])
    if fragment.get("header_mode") == "headerless":
        headers = []
        value_number = 0
        for column in fragment["logical_columns"]:
            if column["role"] == "row_label":
                headers.append("Row label")
            else:
                value_number += 1
                headers.append(f"Value {value_number}")
    seen = Counter()
    result = []
    for index, value in enumerate(headers):
        base = value or ("Row label" if fragment["logical_columns"][index]["role"] == "row_label" else f"Value {index + 1}")
        key = normalize_text(base).casefold()
        seen[key] += 1
        result.append(base if seen[key] == 1 else f"{base} ({seen[key]})")
    return result


def markdown_table_lines(fragment: dict) -> list[str]:
    headers = _display_headers(fragment)
    width = len(headers)
    lines = ["| " + " | ".join(_escape_markdown(value) for value in headers) + " |"]
    alignments = []
    for column, unit in zip(fragment["logical_columns"], fragment["logical_column_units"]):
        alignments.append(":---" if column["role"] == "row_label" or unit == "text" else "---:")
    lines.append("| " + " | ".join(alignments) + " |")
    for row in fragment["logical_rows"]:
        padded = list(row[:width]) + [""] * max(0, width - len(row))
        lines.append("| " + " | ".join(_escape_markdown(value) for value in padded) + " |")
    return lines


def render_logical_table(fragment: dict) -> str:
    lines = []
    if fragment.get("title"):
        lines.append(fragment["title"])
    if fragment.get("units") and (not fragment.get("title") or fragment["units"].casefold() not in fragment["title"].casefold()):
        lines.append(f"Units: {fragment['units']}")
    if fragment.get("logical_header_context"):
        lines.append("Header context: " + " — ".join(fragment["logical_header_context"]))
    if lines:
        lines.append("")
    lines.extend(markdown_table_lines(fragment))
    return "\n".join(lines)


def render_table_text(rows: list[list[str]], *, title: str | None = None, units: str | None = None) -> str:
    """Deprecated renderer retained only for old synthetic callers."""
    lines = [value for value in (title, units) if value]
    lines.extend(" | ".join(str(value or "") for value in row) for row in rows if any(row))
    return "\n".join(lines)


def group_list_rows(rows: list[list[str]]) -> list[dict]:
    """Group bullet-table rows into semantic list items."""
    items = []
    for row_index, row in enumerate(rows):
        values = [value for value in row if value]
        has_bullet = any(TABLE_BULLET_PATTERN.fullmatch(value) for value in values)
        text = " ".join(value for value in values if not TABLE_BULLET_PATTERN.fullmatch(value))
        if not text:
            continue
        if has_bullet or not items:
            items.append({"text": text, "row_indexes": [row_index]})
        else:
            items[-1]["text"] = normalize_text(f"{items[-1]['text']} {text}")
            items[-1]["row_indexes"].append(row_index)
    return items


def is_semantic_bullet_table(structure: dict) -> bool:
    rows = structure["physical_rows"]
    if not rows:
        return False
    bullet_rows = sum(
        any(TABLE_BULLET_PATTERN.fullmatch(value) for value in row if value)
        for row in rows
    )
    return bullet_rows / len(rows) >= 0.60


def canonical_signature(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


CONTINUATION_NEUTRAL_UNITS = frozenset({None, "text", "number", "unknown", "mixed"})


def has_reliable_logical_units(units: list[str] | tuple[str, ...]) -> bool:
    return any(unit not in CONTINUATION_NEUTRAL_UNITS for unit in units)


def native_fragment_context(fragment: dict) -> dict:
    paths = fragment.get("logical_header_paths") or []
    roles = [column["role"] for column in fragment.get("logical_columns") or []]
    missing = []
    if not fragment.get("title"):
        missing.append("title")
    if fragment.get("header_mode") == "headerless" or not any(paths):
        missing.append("header")
    if not has_reliable_logical_units(fragment.get("logical_column_units") or []):
        missing.append("units")
    return {
        "title": fragment.get("title"),
        "title_source": fragment.get("title_source", "none"),
        "header_mode": fragment.get("header_mode"),
        "header_signature": canonical_signature({"paths": paths, "roles": roles}),
        "unit_signature": canonical_signature(fragment.get("logical_column_units") or []),
        "provisional_table_kind": fragment.get("table_kind"),
        "provisional_classification_reasons": fragment.get("classification_reasons") or [],
        "missing_fields": missing,
    }


def _normalized_label_set(fragment: dict) -> set[str]:
    return {
        re.sub(r"\s+", " ", row[0]).strip().casefold()
        for row, role in zip(fragment.get("logical_rows") or [], fragment.get("logical_row_roles") or [])
        if row and row[0] and role not in {"section_label", "footnote"}
    }


def _set_overlap(left: set[str], right: set[str]) -> float:
    return len(left & right) / min(len(left), len(right)) if left and right else 0.0


def _header_similarity(left: list[list[str]], right: list[list[str]]) -> float:
    left_values = Counter(normalize_text(" — ".join(path)).casefold() for path in left if path)
    right_values = Counter(normalize_text(" — ".join(path)).casefold() for path in right if path)
    if not left_values or not right_values:
        return 0.0
    intersection = sum((left_values & right_values).values())
    union = sum((left_values | right_values).values())
    return intersection / union if union else 0.0


def has_explicit_continued_cue(*values: str | None) -> bool:
    """Return whether filing context contains the bounded ``(Continued)`` cue."""

    return any(
        re.search(r"\(\s*continued\s*\)", value or "", re.IGNORECASE)
        for value in values
    )


def link_table_continuation(current: dict, previous: dict | None, *, intervening_meaningful_blocks: int = 0) -> dict:
    if not previous:
        return {"accepted": False, "reasons": ["no_previous_table"], "rejection_reasons": []}
    rejection = []
    if intervening_meaningful_blocks > TABLE_HEURISTICS["continuation_max_intervening_meaningful_blocks"]:
        rejection.append("intervening_semantic_content")
    if current.get("document_region") != previous.get("document_region"):
        rejection.append("document_region_conflict")
    if current.get("section") != previous.get("section") and current.get("document_region") != previous.get("document_region"):
        rejection.append("section_conflict")
    current_title = normalize_text(current.get("title") or "").casefold()
    previous_title = normalize_text(previous.get("title") or "").casefold()
    continued_cue = has_explicit_continued_cue(
        current_title,
        *(current.get("continuation_cues") or []),
    )
    if current_title and previous_title and current_title != previous_title and not continued_cue:
        rejection.append("conflicting_explicit_title")
    current_kind = current.get("table_kind")
    previous_kind = previous.get("table_kind")
    family = {"financial_data": "financial", "structured_text": "structured", "exhibit_list": "registry", "index_navigation": "registry", "layout": "layout", "unknown": "unknown"}
    if current_kind and previous_kind and "unknown" not in {family.get(current_kind), family.get(previous_kind)} and family.get(current_kind) != family.get(previous_kind):
        rejection.append("table_kind_family_conflict")

    current_labels = _normalized_label_set(current)
    previous_labels = _normalized_label_set(previous)
    row_overlap = _set_overlap(current_labels, previous_labels)
    header_similarity = _header_similarity(current.get("logical_header_paths") or [], previous.get("logical_header_paths") or [])
    current_roles = [column["role"] for column in current.get("logical_columns") or []]
    previous_roles = [column["role"] for column in previous.get("logical_columns") or []]
    lane_compatible = (
        current.get("logical_width") == previous.get("logical_width")
        and current_roles == previous_roles
    )
    current_native_units = tuple(current.get("logical_column_units") or [])
    previous_native_units = tuple(previous.get("logical_column_units") or [])
    unit_conflicts = [
        (current_unit, previous_unit)
        for current_unit, previous_unit in zip(
            current_native_units, previous_native_units, strict=False
        )
        if current_unit not in CONTINUATION_NEUTRAL_UNITS
        and previous_unit not in CONTINUATION_NEUTRAL_UNITS
        and current_unit != previous_unit
    ]
    if unit_conflicts:
        rejection.append("native_unit_conflict")
    current_header_context = tuple(
        normalize_text(value).casefold()
        for value in current.get("logical_header_context") or []
    )
    previous_header_context = tuple(
        normalize_text(value).casefold()
        for value in previous.get("logical_header_context") or []
    )
    different_period_context = bool(
        current_header_context
        and previous_header_context
        and current_header_context != previous_header_context
        and any(TABLE_PERIOD_PATTERN.search(value) for value in current_header_context)
        and any(TABLE_PERIOD_PATTERN.search(value) for value in previous_header_context)
    )
    reasons = []
    if continued_cue:
        reasons.append("explicit_continued_cue")
    if (
        row_overlap >= TABLE_HEURISTICS["continuation_row_label_overlap"]
        and different_period_context
    ):
        reasons.append("row_label_overlap_with_different_period")
    if (
        header_similarity >= TABLE_HEURISTICS["continuation_header_path_similarity"]
        and row_overlap < TABLE_HEURISTICS["continuation_row_label_overlap"]
        and bool(current_labels)
        and bool(previous_labels)
    ):
        reasons.append("repeated_headers_with_complementary_rows")
    current_missing = set((current.get("native_context") or {}).get("missing_fields") or [])
    if lane_compatible and current_missing & {"title", "header"}:
        reasons.append("missing_context_with_compatible_schema")
    if intervening_meaningful_blocks == 0:
        reasons.append("adjacent")
    strong_positive = (
        "explicit_continued_cue" in reasons
        or "row_label_overlap_with_different_period" in reasons
        or "repeated_headers_with_complementary_rows" in reasons
    )
    accepted = not rejection and strong_positive and lane_compatible
    if accepted:
        rejection_reasons = []
    elif rejection:
        rejection_reasons = rejection
    elif not lane_compatible:
        rejection_reasons = ["lane_signature_conflict"]
    else:
        rejection_reasons = ["no_strong_positive_signal"]
    return {
        "accepted": accepted,
        "reasons": reasons if accepted else [],
        "rejection_reasons": rejection_reasons,
        "row_label_overlap": row_overlap,
        "header_path_similarity": header_similarity,
        "different_period_context": different_period_context,
    }


def apply_inherited_context(fragment: dict, previous: dict, continuation: dict) -> dict:
    inherited = {"title_from_block_id": None, "header_from_block_id": None, "units_from_block_id": None}
    if not continuation["accepted"]:
        return inherited
    previous_block_id = previous["block_id"]
    if not fragment.get("title") and previous.get("title"):
        fragment.update(
            title=previous["title"],
            title_source="inherited",
            title_source_block_id=previous_block_id,
            title_source_raw_cell_ids=[],
            title_source_locator=f"block:{previous_block_id}",
            title_confidence=previous.get("title_confidence", 0.8),
            title_quality_status="inherited",
        )
        inherited["title_from_block_id"] = previous_block_id
    if (fragment.get("header_mode") == "headerless" or not any(fragment.get("logical_header_paths") or [])) and any(previous.get("logical_header_paths") or []):
        for field in (
            "header_mode",
            "header_confidence",
            "header_reasons",
            "logical_header_rows",
            "logical_header_paths",
            "logical_header_context",
            "logical_header_context_source_raw_cell_ids",
            "logical_column_headers",
            "logical_column_header_metadata",
        ):
            fragment[field] = previous.get(field)
        fragment["header_source_block_id"] = previous_block_id
        inherited["header_from_block_id"] = previous_block_id
    if (
        not has_reliable_logical_units(fragment.get("logical_column_units") or [])
        and previous.get("logical_column_units")
    ):
        fragment["logical_column_units"] = previous["logical_column_units"]
        fragment["logical_column_unit_metadata"] = [
            dict(value, inherited_from_block_id=previous_block_id)
            for value in previous["logical_column_unit_metadata"]
        ]
        fragment["units"] = previous.get("units")
        inherited["units_from_block_id"] = previous_block_id
    return inherited


def _split_markdown_row(line: str) -> list[str]:
    """Split a rendered row on unescaped pipes only."""
    body = line[1:-1] if line.startswith("|") and line.endswith("|") else line
    values = []
    current = []
    backslashes = 0
    for character in body:
        if character == "|" and backslashes % 2 == 0:
            values.append("".join(current))
            current = []
            backslashes = 0
            continue
        current.append(character)
        if character == "\\":
            backslashes += 1
        else:
            backslashes = 0
    values.append("".join(current))
    return values


def validate_markdown(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.startswith("|")]
    if len(lines) < 2:
        return False
    delimiter_indexes = [
        index
        for index, line in enumerate(lines)
        if all(
            re.fullmatch(r":?-{3,}:?", value.strip())
            for value in _split_markdown_row(line)
        )
    ]
    if not delimiter_indexes:
        return False
    widths = [len(_split_markdown_row(line)) for line in lines]
    return len(set(widths)) == 1


def validate_logical_table(fragment: dict, *, strict: bool = True) -> None:
    width = fragment.get("logical_width", 0)
    if width <= 0:
        raise ValueError("logical_width must be positive")
    for field in (
        "logical_column_headers",
        "logical_column_header_metadata",
        "logical_header_paths",
        "logical_column_units",
        "logical_column_unit_metadata",
        "logical_columns",
    ):
        if len(fragment.get(field) or []) != width:
            raise ValueError(f"{field} does not match logical_width")
    for field in ("logical_header_rows", "logical_rows"):
        if any(len(row) != width for row in fragment.get(field) or []):
            raise ValueError(f"{field} is not rectangular")
    rows = fragment.get("logical_rows") or []
    for field in ("logical_cell_sources", "logical_cell_states"):
        matrix = fragment.get(field) or []
        if len(matrix) != len(rows) or any(len(row) != width for row in matrix):
            raise ValueError(f"{field} does not match logical_rows")
    if len(fragment.get("logical_row_source_indexes") or []) != len(rows) or len(fragment.get("logical_row_roles") or []) != len(rows):
        raise ValueError("logical row metadata does not match logical_rows")
    if any(all(not row[column] for row in rows) and all(not row[column] for row in fragment.get("logical_header_rows") or []) for column in range(width)):
        raise ValueError("logical table contains an entirely empty column")
    markers = set(CURRENCY_MARKERS) | {"%"}
    if any(value.strip() in markers for row in rows for value in row):
        raise ValueError("logical table contains a standalone marker cell")
    diagnostics = fragment.get("normalization_diagnostics") or {}
    if diagnostics.get("collisions"):
        raise ValueError("logical normalization contains unresolved collisions")
    if diagnostics.get("unmapped_nonempty_raw_cell_ids"):
        raise ValueError("logical normalization contains unmapped cells")
    if strict and not validate_markdown(fragment.get("text", "")):
        raise ValueError("logical table Markdown is invalid")


def table_quality_metrics(fragment: dict) -> dict:
    diagnostics = fragment["normalization_diagnostics"]
    data_rows = [row for row, role in zip(fragment["logical_rows"], fragment["logical_row_roles"]) if role not in {"section_label", "footnote"}]
    denominator = len(data_rows) * fragment["logical_width"]
    logical_empty = sum(not value for row in data_rows for value in row) / denominator if denominator else 0.0
    raw_ids = {cell["raw_cell_id"] for cell in fragment["raw_cells"] if cell["text"]}
    mapped = {
        raw_id
        for row in fragment["logical_cell_sources"]
        for cell in row
        for raw_id in cell
    }
    mapped.update(raw_id for metadata in fragment["logical_column_header_metadata"] for raw_id in metadata["source_raw_cell_ids"])
    mapped.update(fragment.get("title_source_raw_cell_ids") or [])
    mapped.update(raw_id for metadata in fragment["logical_column_unit_metadata"] for raw_id in metadata["source_raw_cell_ids"])
    mapped.update(item["raw_cell_id"] for item in diagnostics.get("ignored_raw_cells", []))
    return {
        "source_coordinate_empty_density": diagnostics["source_coordinate_empty_density"],
        "physical_display_empty_density": diagnostics["physical_display_empty_density"],
        "logical_empty_density": logical_empty,
        "source_coordinate_to_logical_width_ratio": diagnostics["source_coordinate_to_logical_width_ratio"],
        "physical_display_to_logical_width_ratio": diagnostics["physical_display_to_logical_width_ratio"],
        "logical_to_physical_display_fraction": diagnostics["logical_to_physical_display_fraction"],
        "logical_width_consistency": 1.0,
        "raw_cell_accounting_coverage": len(raw_ids & mapped) / len(raw_ids) if raw_ids else 1.0,
        "standalone_marker_count": sum(value.strip() in set(CURRENCY_MARKERS) | {"%"} for row in fragment["logical_rows"] for value in row),
        "markdown_valid": validate_markdown(fragment["text"]),
        "fallback_used": diagnostics.get("fallback_used", False),
    }


LXML_VERSION = lxml.__version__
