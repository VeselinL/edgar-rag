from dataclasses import dataclass, field

from .dom_processing import (
    collect_visible_text,
    find_source_anchor,
    identify_item_section,
    is_leaf_content_div,
    is_page_furniture,
    is_subheading_candidate,
    normalize_text,
    raw_tag,
    text_excluding_descendants,
)
from .table_processing import (
    HTML_TABLE_FINGERPRINT_VERSION,
    TABLE_HEURISTICS_VERSION,
    TABLE_SCHEMA_VERSION,
    apply_inherited_context,
    build_column_headers,
    build_row_profiles,
    classify_logical_table,
    detect_table_header_rows,
    extract_column_units,
    extract_table_structure,
    finalize_logical_headers,
    group_list_rows,
    has_explicit_continued_cue,
    identify_promoted_rows,
    infer_logical_column_units,
    is_semantic_bullet_table,
    link_table_continuation,
    native_fragment_context,
    normalize_logical_columns,
    project_logical_headers,
    render_logical_table,
    select_table_title,
    table_fingerprint,
)


REGION_LABELS = {
    "filing_body": "Filing body",
    "financial_statements": "Financial statements",
    "financial_statement_notes": "Financial statement notes",
    "financial_statement_schedules": "Financial statement schedules",
    "exhibits": "Exhibits",
    "signatures": "Signatures",
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
    document_region: str = "filing_body"
    blocks: list[dict] = field(default_factory=list)
    content_started: bool = False
    html_table_index: int = 0
    last_table_block: dict | None = None
    meaningful_blocks_since_last_table: int = 0


def effective_section_path(context: ExtractionContext) -> list[str]:
    path = [context.section]
    include_subsection = bool(context.subsection)
    if context.document_region in {"exhibits", "signatures"} and context.subsection:
        region_token = context.document_region.removesuffix("s")
        include_subsection = region_token in context.subsection.casefold()
    if include_subsection:
        path.append(context.subsection)
    if context.document_region != "filing_body":
        region_label = REGION_LABELS[context.document_region]
        if all(region_label.casefold() not in value.casefold() for value in path):
            path.append(region_label)
    return path


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
        "document_region": context.document_region,
        "effective_section_path": effective_section_path(context),
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
    if content_type not in {"data_table", "text_table", "unknown_table", "navigation"}:
        if context.last_table_block is not None:
            context.meaningful_blocks_since_last_table += 1
    return block


def _region_for_item(section: str) -> str:
    number = section.split("—", 1)[0].strip().casefold()
    if number == "item 8":
        return "financial_statements"
    return "filing_body"


def update_document_region(text: str, context: ExtractionContext, *, item_heading: bool = False) -> None:
    """Update effective context without rewriting literal SEC Item provenance."""
    value = normalize_text(text).strip().rstrip(":")
    upper = value.upper()
    if item_heading:
        context.document_region = _region_for_item(context.section)
        return
    if not value or len(value) > 250:
        return
    if upper in {"SIGNATURES", "POWER OF ATTORNEY"}:
        context.document_region = "signatures"
        return
    if re_match(r"^(?:EXHIBITS?|EXHIBIT INDEX)$", upper):
        context.document_region = "exhibits"
        return
    if re_match(r"^(?:FINANCIAL STATEMENT SCHEDULES|SCHEDULE II\b.*)$", upper):
        context.document_region = "financial_statement_schedules"
        return
    if re_match(r"^NOTES? TO (?:THE )?(?:CONSOLIDATED )?FINANCIAL STATEMENTS(?: \(CONTINUED\))?$", upper):
        context.document_region = "financial_statement_notes"
        return
    if re_match(
        r"^(?:CONSOLIDATED |COMBINED )?(?:STATEMENTS? OF .+|BALANCE SHEETS?|STATEMENTS? OF CASH FLOWS|STATEMENTS? OF STOCKHOLDERS['’]? EQUITY)$",
        upper,
    ):
        context.document_region = "financial_statements"
        return
    if context.document_region in {
        "financial_statements",
        "financial_statement_notes",
        "financial_statement_schedules",
    } and re_match(r"^NOTE\s+\d+[A-Z]?(?:\.|\s*[-—–])\s*.+", upper):
        context.document_region = "financial_statement_notes"


def re_match(pattern: str, value: str) -> bool:
    import re

    return bool(re.match(pattern, value, re.IGNORECASE))


def emit_paragraph(node, context: ExtractionContext) -> dict | None:
    """Emit one paragraph boundary, including all inline descendant text."""
    text = collect_visible_text(node)
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
        update_document_region(text, context, item_heading=True)
        content_type = "heading"
        extra = {"heading_kind": "item", "heading_level": 1}
    elif is_subheading_candidate(node, text):
        context.subsection = text
        update_document_region(text, context)
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


def emit_heading(node, context: ExtractionContext) -> dict | None:
    """Emit a semantic HTML heading and update Item and region state."""
    text = collect_visible_text(node)
    if not text:
        return None

    source_anchor = find_source_anchor(node)
    section = identify_item_section(node, text)
    if section:
        context.section = section
        context.subsection = None
        context.section_anchor = source_anchor
        update_document_region(text, context, item_heading=True)
        heading_kind = "item"
        heading_level = 1
    else:
        context.subsection = text
        update_document_region(text, context)
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


def _html_table_identity(node, context: ExtractionContext, html_table_index: int) -> dict:
    html_table_id = f"{context.ticker}-{context.filing_year}-HTMLTABLE-{html_table_index:04d}"
    return {
        "html_table_id": html_table_id,
        "html_table_index": html_table_index,
        "html_table_xpath": node.getroottree().getpath(node),
        "html_table_fingerprint": table_fingerprint(node),
        "html_table_fingerprint_version": HTML_TABLE_FINGERPRINT_VERSION,
    }


def emit_table(
    node,
    context: ExtractionContext,
    *,
    html_table_index: int | None = None,
) -> dict | list[dict] | None:
    """Normalize, classify, contextualize, and emit one source table fragment."""
    if html_table_index is None:
        context.html_table_index += 1
        html_table_index = context.html_table_index
    identity = _html_table_identity(node, context, html_table_index)
    structure = extract_table_structure(node, identity["html_table_id"])
    rows = structure["physical_rows"]
    if not rows:
        return None

    source_anchor = find_source_anchor(node)
    if is_semantic_bullet_table(structure):
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
                        **identity,
                        "table_class": "list",
                        "table_kind": "semantic_list",
                        "classification_reasons": ["semantic_bullets"],
                        "table_row_indexes": item["row_indexes"],
                        "rows": [rows[index] for index in item["row_indexes"]],
                    },
                )
            )
        context.last_table_block = None
        context.meaningful_blocks_since_last_table = 0
        return blocks

    promoted = identify_promoted_rows(structure)
    excluded_rows = set(promoted["internal_title_rows"] + promoted["unit_rows"])
    row_profiles = build_row_profiles(structure, excluded_rows=excluded_rows)
    header = detect_table_header_rows(
        structure,
        row_profiles,
        excluded_rows=excluded_rows,
    )
    logical = normalize_logical_columns(
        structure,
        header,
        row_profiles,
        promoted_rows=promoted,
    )
    header_projection = project_logical_headers(structure, logical, header)
    source_rows = {
        row_index: [
            cell
            for cell in structure["raw_cells"]
            if cell["row"] == row_index and cell["text"]
        ]
        for row_index in promoted["internal_title_rows"]
    }
    internal_title_cells = [cell for cells in source_rows.values() for cell in cells]
    title = select_table_title(
        node,
        context.blocks,
        internal_title_cells,
        company=context.company,
        document_region=context.document_region,
    )
    units = infer_logical_column_units(
        structure,
        logical,
        header_projection,
        title=title["title"],
        title_source_block_id=title["title_source_block_id"],
    )
    logical_headers = finalize_logical_headers(
        logical,
        header_projection,
        units["logical_column_units"],
    )
    predicted_block_id = f"{context.ticker}-{context.filing_year}-{len(context.blocks) + 1:06d}"
    fragment = {
        **identity,
        **logical,
        **header,
        **logical_headers,
        **units,
        **title,
        "table_schema_version": TABLE_SCHEMA_VERSION,
        "table_heuristics_version": TABLE_HEURISTICS_VERSION,
        "document_region": context.document_region,
        "effective_section_path": effective_section_path(context),
        "section": context.section,
        "block_id": predicted_block_id,
        "header_source_block_id": None,
        "continuation_cues": [
            block["text"]
            for block in context.blocks[-2:]
            if has_explicit_continued_cue(block.get("text"))
        ],
    }
    provisional = classify_logical_table(
        node,
        structure,
        fragment,
        section=context.section,
        document_region=context.document_region,
        title=fragment.get("title"),
    )
    fragment.update(provisional)
    if provisional["table_kind"] == "exhibit_list" and context.document_region != "exhibits":
        # An exhibit registry is itself high-confidence region evidence.  This
        # handles filings that begin the registry without a separate "Exhibits"
        # heading and prevents the preceding financial-schedule heading from
        # leaking into the registry title or later exhibit fragments.
        previous_region = context.document_region
        context.document_region = "exhibits"
        fragment["document_region"] = "exhibits"
        fragment["effective_section_path"] = effective_section_path(context)
        title_source_id = fragment.get("title_source_block_id")
        title_source_block = next(
            (
                block
                for block in context.blocks
                if block.get("block_id") == title_source_id
            ),
            None,
        )
        if (
            title_source_block is not None
            and title_source_block.get("document_region") != "exhibits"
            and fragment.get("title_source") != "prose_caption"
        ):
            fragment["rejected_title_candidates"] = [
                {
                    "text": fragment.get("title"),
                    "source": fragment.get("title_source"),
                    "reason_codes": [
                        f"region_kind_conflict:{previous_region}->exhibits"
                    ],
                },
                *fragment.get("rejected_title_candidates", []),
            ][:5]
            fragment.update(
                title=None,
                title_source="none",
                title_source_block_id=None,
                title_source_raw_cell_ids=[],
                title_source_locator=None,
                title_confidence=0.0,
                title_quality_status="missing",
            )
    fragment["native_context"] = native_fragment_context(fragment)

    continuation = link_table_continuation(
        fragment,
        context.last_table_block,
        intervening_meaningful_blocks=context.meaningful_blocks_since_last_table,
    )
    previous = context.last_table_block
    if continuation["accepted"] and previous is not None:
        logical_table_id = previous["logical_table_id"]
        fragment_index = previous["table_fragment_index"] + 1
        inherited = apply_inherited_context(fragment, previous, continuation)
        continued_from = previous["block_id"]
    else:
        logical_table_id = f"{context.ticker}-{context.filing_year}-TABLE-{html_table_index:04d}"
        fragment_index = 1
        inherited = {
            "title_from_block_id": None,
            "header_from_block_id": None,
            "units_from_block_id": None,
        }
        continued_from = None
    fragment.update(
        {
            "logical_table_id": logical_table_id,
            "table_fragment_index": fragment_index,
            "is_continuation": continuation["accepted"],
            "continued_from_block_id": continued_from,
            "continuation_mode_hint": None,
            "continuation_reasons": continuation["reasons"],
            "continuation_rejection_reasons": continuation["rejection_reasons"],
            "inherited_context": inherited,
        }
    )
    final_classification = classify_logical_table(
        node,
        structure,
        fragment,
        section=context.section,
        document_region=context.document_region,
        title=fragment.get("title"),
    )
    fragment.update(final_classification)
    fragment["text"] = render_logical_table(fragment)

    # Header cells that could not project are still explicit header evidence and
    # remain auditable rather than disappearing from source accounting.
    existing_ignored = fragment["normalization_diagnostics"]["ignored_raw_cells"]
    for raw_id in header_projection["unprojected_header_raw_cell_ids"]:
        existing_ignored.append(
            {
                "raw_cell_id": raw_id,
                "reason_code": "promoted_header_context",
                "equivalent_raw_cell_id": None,
                "promoted_to": "header",
                "note": "Header evidence did not intersect a body-supported lane.",
            }
        )
    for raw_id in header_projection["unit_header_raw_cell_ids"]:
        existing_ignored.append(
            {
                "raw_cell_id": raw_id,
                "reason_code": "promoted_unit_row",
                "equivalent_raw_cell_id": None,
                "promoted_to": "unit",
                "note": None,
            }
        )

    physical_header_indexes = set(header["header_row_indexes"])
    extra = {
        **fragment,
        "raw_rows": structure["raw_rows"],
        "raw_cells": structure["raw_cells"],
        "physical_rows": structure["physical_rows"],
        "physical_expanded_rows": structure["physical_expanded_rows"],
        "physical_source_row_indexes": structure["physical_source_row_indexes"],
        "physical_source_column_indexes": structure["physical_source_column_indexes"],
        # Deprecated aliases remain explicitly physical during the transition.
        "rows": structure["physical_rows"],
        "expanded_rows": structure["physical_expanded_rows"],
        "source_row_indexes": structure["physical_source_row_indexes"],
        "source_column_indexes": structure["physical_source_column_indexes"],
        "header_row_indexes": header["header_row_indexes"],
        "column_headers": build_column_headers(structure, header),
        "column_units": extract_column_units(structure["physical_rows"]),
        "data_rows": [
            row
            for index, row in enumerate(structure["physical_rows"])
            if index not in physical_header_indexes
        ],
    }
    block = append_block(
        context,
        content_type=fragment["content_type"],
        text=fragment["text"],
        source_tag="table",
        source_anchor=source_anchor,
        extra=extra,
    )
    context.last_table_block = block
    context.meaningful_blocks_since_last_table = 0
    return block


def visit_node(node, context: ExtractionContext) -> None:
    """Walk the DOM in order and emit each semantic boundary exactly once."""
    tag = raw_tag(node)

    if is_page_furniture(node):
        return

    if tag == "table":
        context.html_table_index += 1
        if context.content_started:
            emit_table(node, context, html_table_index=context.html_table_index)
        return

    is_text_block = (
        tag in {"p", "h1", "h2", "h3", "h4", "h5", "h6"}
        or (tag == "div" and is_leaf_content_div(node))
    )

    if is_text_block and not context.content_started:
        text = collect_visible_text(node)
        section = identify_item_section(node, text)
        is_toc_link = bool(node.xpath(".//a[starts-with(@href, '#')]") )

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
        emit_paragraph(node, context)
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
