"""Normalize internal retrieval chunks into user-facing source objects."""

from __future__ import annotations

from typing import Any


class SourceNormalizationError(ValueError):
    """Raised when a selected chunk cannot be represented faithfully."""


def _common_source(chunk: dict[str, Any]) -> dict[str, Any]:
    required = ("company", "ticker", "filing_year", "section")
    missing = [key for key in required if chunk.get(key) in (None, "")]
    if missing:
        raise SourceNormalizationError(
            f"Source chunk is missing required metadata: {', '.join(missing)}"
        )
    source = {
        "company": str(chunk["company"]),
        "ticker": str(chunk["ticker"]),
        "filing_year": int(chunk["filing_year"]),
        "section": str(chunk["section"]),
    }
    if chunk.get("source_url"):
        source["source_url"] = str(chunk["source_url"])
    return source


def normalize_source(result: dict[str, Any]) -> dict[str, Any]:
    chunk = result.get("chunk", result)
    source = _common_source(chunk)
    if chunk.get("content_type") != "table":
        text = chunk.get("text")
        if not isinstance(text, str) or not text:
            raise SourceNormalizationError("Narrative source has no text.")
        return {**source, "content_type": "text", "text": text}

    headers = chunk.get("logical_column_headers")
    rows = chunk.get("logical_rows")
    if not isinstance(headers, list) or not headers or not all(
        isinstance(header, str) for header in headers
    ):
        raise SourceNormalizationError("Table source has no validated logical headers.")
    if not isinstance(rows, list) or not all(isinstance(row, list) for row in rows):
        raise SourceNormalizationError("Table source has no validated logical rows.")
    width = len(headers)
    if any(len(row) != width for row in rows):
        raise SourceNormalizationError("Table source rows are not rectangular.")
    if any(not isinstance(cell, str) for row in rows for cell in row):
        raise SourceNormalizationError("Table source contains a non-string cell.")

    table = {
        **source,
        "content_type": "table",
        "headers": headers,
        "rows": rows,
    }
    for field in ("title", "units"):
        if chunk.get(field) is not None:
            table[field] = str(chunk[field])
    column_units = chunk.get("logical_column_units")
    if isinstance(column_units, list) and len(column_units) == width:
        table["column_units"] = [str(unit) if unit is not None else "" for unit in column_units]
    return table


def normalize_sources(evidence: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    sources = []
    malformed_count = 0
    for result in evidence:
        try:
            sources.append(normalize_source(result))
        except SourceNormalizationError:
            malformed_count += 1
    return sources, malformed_count
