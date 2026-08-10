from dataclasses import dataclass, field

from .dom_processing import (
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
    build_column_headers,
    classify_table,
    detect_table_header_rows,
    extract_column_units,
    extract_table_structure,
    extract_table_units,
    find_table_title,
    group_list_rows,
    render_table_text,
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
    subsection: str | None = None
    section_anchor: str | None = None
    blocks: list[dict] = field(default_factory=list)
    content_started: bool = False


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
    column_units = extract_column_units(rows) if table_class == "data" else []
    units = extract_table_units(rows, column_units) if table_class == "data" else None
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
                "column_units": column_units,
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
