import copy
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.chunking.chunk_documents import chunk_blocks, count_tokens, get_tokenizer
from src.filings.table_processing import TABLE_HEURISTICS_VERSION, render_logical_table


CONFIG = {
    "schema_version": 3,
    "table_schema_version": 2,
    "table_heuristics_version": TABLE_HEURISTICS_VERSION,
    "chunk_size": 40,
    "chunk_overlap": 8,
    "length_function": "tokens",
    "tokenizer_model": "sentence-transformers/all-MiniLM-L6-v2",
    "tokenizer_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    "separators": ["\n\n", "\n", ". ", " ", ""],
    "excluded_content_types": ["navigation"],
    "narrative_content_types": ["heading", "paragraph", "list_item"],
    "table_content_types": ["data_table", "text_table", "unknown_table"],
}
TOKENIZER = get_tokenizer(CONFIG)


def block(index: int, content_type: str, text: str, section: str = "Item 1"):
    return {
        "block_id": f"MBLY-2025-{index:06d}",
        "block_index": index,
        "company": "Mobileye Global Inc.",
        "ticker": "MBLY",
        "cik": "0001910139",
        "form": "10-K",
        "filing_year": 2025,
        "filing_date": "2026-02-12",
        "reporting_period": "2025-12-27",
        "accession_number": "test-accession",
        "section": section,
        "section_path": [section],
        "document_region": "financial_statements" if section == "Item 8" else "filing_body",
        "effective_section_path": [section],
        "content_type": content_type,
        "text": text,
        "source_anchor": f"anchor-{index}",
        "page_start": None,
        "page_end": None,
        "source_url": "https://example.com/filing.htm",
    }


def table_block(
    index: int,
    *,
    logical_id: str = "MBLY-2025-LTABLE-000001",
    headers: list[str] | None = None,
    header_paths: list[list[str]] | None = None,
    rows: list[list[str]] | None = None,
    units: list[str] | None = None,
    fragment_index: int = 1,
    header_mode: str = "explicit",
    title: str | None = "Balance Sheets",
):
    headers = headers or ["Line item", "2025"]
    header_paths = header_paths or [["Line item"], ["2025", "Amount"]]
    rows = rows or [["Cash", "$100"], ["Total assets", "$500"]]
    units = units or ["text", "usd_millions"]
    width = len(headers)
    value = block(index, "data_table", "PHYSICAL RENDERING MUST NOT BE USED", "Item 8")
    value.update(
        {
            "table_schema_version": 2,
            "table_heuristics_version": TABLE_HEURISTICS_VERSION,
            "table_class": "data",
            "table_kind": "financial_data",
            "logical_table_id": logical_id,
            "html_table_id": f"MBLY-2025-HTMLTABLE-{index:04d}",
            "html_table_index": index,
            "html_table_xpath": f"/html/body/table[{index}]",
            "html_table_fingerprint": f"sha256:fixture-{index}",
            "html_table_fingerprint_version": "cleaned-lxml-html-v1",
            "table_fragment_index": fragment_index,
            "title": title,
            "units": "usd_millions" if "usd_millions" in units else None,
            "header_mode": header_mode,
            "logical_width": width,
            "logical_header_rows": [] if header_mode == "headerless" else [headers],
            "logical_header_paths": header_paths,
            "logical_header_context": [],
            "logical_header_context_source_raw_cell_ids": [],
            "logical_column_headers": headers,
            "logical_column_header_metadata": [
                {
                    "source_raw_cell_ids": [f"T{index}-H{column}"],
                    "generated_components": [],
                    "inherited_from_block_id": None,
                }
                for column in range(width)
            ],
            "logical_column_units": units,
            "logical_column_unit_metadata": [
                {
                    "source_kind": "fixture",
                    "source_block_ids": [],
                    "source_raw_cell_ids": [],
                    "inherited_from_block_id": None,
                    "confidence": 1.0,
                    "reason_code": "fixture",
                }
                for _ in range(width)
            ],
            "logical_columns": [
                {
                    "logical_index": column,
                    "physical_intervals": [[column, column + 1]],
                    "role": "row_label" if column == 0 else "value",
                    "unit": units[column],
                }
                for column in range(width)
            ],
            "logical_rows": rows,
            "logical_row_source_indexes": list(range(10, 10 + len(rows))),
            "logical_row_roles": ["data"] * len(rows),
            "logical_cell_states": [
                ["present" if cell else "missing_blank_in_aligned_lane" for cell in row]
                for row in rows
            ],
            "logical_cell_sources": [
                [
                    [f"T{index}-R{row_number}-C{column}"] if cell else []
                    for column, cell in enumerate(row)
                ]
                for row_number, row in enumerate(rows)
            ],
            "raw_cells": [
                {
                    "raw_cell_id": f"T{index}-R{row_number}-C{column}",
                    "text": cell,
                }
                for row_number, row in enumerate(rows)
                for column, cell in enumerate(row)
                if cell
            ],
            "logical_body_rowspans": [],
            "normalization_diagnostics": {
                "status": "ok",
                "fallback_used": False,
                "fallback_reasons": [],
            },
            # Deliberately incompatible deprecated physical aliases prove that
            # chunk rendering consumes only the logical schema.
            "rows": [["PHYSICAL", "POISON", "EXTRA"]],
            "column_units": ["physical", "physical", "physical"],
        }
    )
    value["text"] = render_logical_table(value)
    return value


class ChunkDocumentTests(unittest.TestCase):
    def test_narrative_chunks_do_not_cross_sections_or_leave_heading_alone(self):
        blocks = [
            block(1, "heading", "Item 1. Business"),
            block(2, "paragraph", "Business evidence."),
            block(3, "heading", "Item 1A. Risk Factors", "Item 1A"),
            block(4, "paragraph", "Risk evidence.", "Item 1A"),
        ]

        chunks = chunk_blocks(blocks, CONFIG)

        self.assertEqual(len(chunks), 2)
        self.assertEqual([chunk["section"] for chunk in chunks], ["Item 1", "Item 1A"])
        self.assertTrue(all(chunk["content_type"] == "narrative" for chunk in chunks))
        self.assertTrue(all(chunk["chunk_schema_version"] == 3 for chunk in chunks))
        self.assertTrue(all("evidence" in chunk["text"] for chunk in chunks))

    def test_oversized_paragraph_is_split_with_source_block_preserved(self):
        paragraph = block(1, "paragraph", "Evidence sentence. " * 30)

        chunks = chunk_blocks([paragraph], CONFIG)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(
            all(count_tokens(chunk["text"], TOKENIZER) <= CONFIG["chunk_size"] for chunk in chunks)
        )
        self.assertTrue(all(chunk["block_ids"] == [paragraph["block_id"]] for chunk in chunks))

    def test_complete_logical_table_is_one_chunk_with_valid_markdown(self):
        table = table_block(1)
        chunks = chunk_blocks([table], {**CONFIG, "chunk_size": 10, "chunk_overlap": 2})

        self.assertEqual(len(chunks), 1)
        chunk = chunks[0]
        self.assertEqual(chunk["content_type"], "table")
        self.assertEqual(chunk["chunk_schema_version"], 3)
        self.assertEqual(chunk["table_schema_version"], 2)
        self.assertEqual(chunk["logical_rows"], table["logical_rows"])
        self.assertEqual(chunk["table_rows"], table["logical_rows"])
        self.assertIn("| Line item | 2025 |", chunk["text"])
        self.assertIn("| :--- | ---: |", chunk["text"])
        self.assertIn("| Total assets | $500 |", chunk["text"])
        self.assertNotIn("POISON", chunk["text"])
        self.assertGreater(count_tokens(chunk["text"], TOKENIZER), 10)

    def test_vertical_fragments_form_one_chunk_and_preserve_provenance(self):
        first = table_block(1, rows=[["Cash", "$100"]], fragment_index=1)
        second = table_block(2, rows=[["Debt", "$40"]], fragment_index=2)

        [chunk] = chunk_blocks([first, second], CONFIG)

        self.assertEqual(chunk["composition_mode"], "vertical")
        self.assertEqual(chunk["table_fragment_count"], 2)
        self.assertEqual(chunk["fragment_block_ids"], [first["block_id"], second["block_id"]])
        self.assertEqual(chunk["html_table_ids"], [first["html_table_id"], second["html_table_id"]])
        self.assertEqual(chunk["logical_rows"], [["Cash", "$100"], ["Debt", "$40"]])
        debt_source = chunk["logical_cell_sources"][1][1][0]
        self.assertEqual(debt_source["source_block_id"], second["block_id"])
        self.assertEqual(debt_source["fragment_index"], 2)

    def test_horizontal_fragments_form_one_rectangular_chunk(self):
        first = table_block(
            1,
            rows=[["Revenue", "$100"], ["Income", "$20"]],
            headers=["Line item", "Amount"],
            header_paths=[["Line item"], ["2025", "Amount"]],
            fragment_index=1,
        )
        second = table_block(
            2,
            rows=[["Revenue", "$90"], ["Income", "$15"]],
            headers=["Line item", "Amount"],
            header_paths=[["Line item"], ["2024", "Amount"]],
            fragment_index=2,
        )

        [chunk] = chunk_blocks([first, second], CONFIG)

        self.assertEqual(chunk["composition_mode"], "horizontal")
        self.assertEqual(chunk["logical_width"], 3)
        self.assertEqual(chunk["logical_column_headers"], ["Line item", "2025 — Amount", "2024 — Amount"])
        self.assertEqual(chunk["logical_rows"][0], ["Revenue", "$100", "$90"])
        self.assertEqual(
            [entry["source_block_id"] for entry in chunk["logical_cell_sources"][0][1:][0]],
            [first["block_id"]],
        )

    def test_unsafe_linked_fragments_render_as_compound_subtables(self):
        first = table_block(1, rows=[["Cash", "$100"]], fragment_index=1)
        second = table_block(
            2,
            headers=["Name", "Date", "Action"],
            header_paths=[["Name"], ["Date"], ["Action"]],
            rows=[["Ada", "January 1, 2025", "Granted"]],
            units=["text", "date", "text"],
            fragment_index=2,
        )

        [chunk] = chunk_blocks([first, second], CONFIG)

        self.assertEqual(chunk["composition_mode"], "compound")
        self.assertEqual(chunk["logical_width"], None)
        self.assertIn("Fragment 1", chunk["text"])
        self.assertIn("Fragment 2", chunk["text"])
        self.assertEqual(len(chunk["logical_fragments"]), 2)

    def test_distinct_logical_ids_never_merge_and_nonlocal_reappearance_fails(self):
        first = table_block(1, logical_id="LT-1")
        second = table_block(2, logical_id="LT-2")
        chunks = chunk_blocks([first, second], CONFIG)
        self.assertEqual([chunk["logical_table_id"] for chunk in chunks], ["LT-1", "LT-2"])

        with self.assertRaisesRegex(ValueError, "reappears nonlocally"):
            chunk_blocks([first, second, copy.deepcopy(first)], CONFIG)

    def test_headerless_table_gets_display_headers_without_consuming_first_row(self):
        table = table_block(
            1,
            headers=["", ""],
            header_paths=[[], []],
            rows=[["2026", "$20"], ["2027", "$17"]],
            header_mode="headerless",
            title="Lease maturities",
        )

        [chunk] = chunk_blocks([table], CONFIG)

        self.assertIn("| Row label | Value 1 |", chunk["text"])
        self.assertIn("| 2026 | $20 |", chunk["text"])
        self.assertEqual(chunk["logical_rows"][0], ["2026", "$20"])

    def test_markdown_escapes_literal_pipes(self):
        table = table_block(1, rows=[["Cash | equivalents", "$100"]])

        [chunk] = chunk_blocks([table], CONFIG)

        self.assertIn(r"Cash \| equivalents", chunk["text"])

    def test_stale_physical_only_table_is_rejected(self):
        stale = block(1, "data_table", "old table", "Item 8")
        stale.update({"table_class": "data", "rows": [["Cash", "$100"]]})

        with self.assertRaisesRegex(ValueError, "table schema version 2"):
            chunk_blocks([stale], CONFIG)

    def test_navigation_is_excluded(self):
        navigation = block(1, "navigation", "Table of contents")
        paragraph = block(2, "paragraph", "Useful evidence.")

        chunks = chunk_blocks([navigation, paragraph], CONFIG)

        self.assertEqual(len(chunks), 1)
        self.assertNotIn("Table of contents", chunks[0]["text"])

    def test_fixed_strategy_respects_size_and_overlap(self):
        config = {**CONFIG, "strategy": "fixed", "chunk_size": 30, "chunk_overlap": 5}
        paragraph = block(1, "paragraph", "evidence " * 100)

        chunks = chunk_blocks([paragraph], config)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(
            all(count_tokens(chunk["text"], TOKENIZER) <= config["chunk_size"] for chunk in chunks)
        )
        self.assertTrue(all(chunk["source_text_start"] < chunk["source_text_end"] for chunk in chunks))
        self.assertTrue(all(chunk["source_token_start"] < chunk["source_token_end"] for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
