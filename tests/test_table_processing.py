import sys
import unittest
from pathlib import Path

from lxml import html as lxml_html


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.filings.preprocess_filing import (
    ExtractionContext,
    classify_table,
    emit_table,
    extract_table_structure,
)


def parse_table(markup: str):
    document = lxml_html.fromstring(f"<html><body>{markup}</body></html>")
    return document.xpath("//table")[0]


class TableProcessingTests(unittest.TestCase):
    def test_span_grid_keeps_alignment_and_drops_fully_empty_column(self):
        table = parse_table(
            """
            <table>
              <tr><th colspan="2">Year ended 2025</th><td></td></tr>
              <tr><td>Revenue</td><td>$ 100</td><td></td></tr>
            </table>
            """
        )

        structure = extract_table_structure(table)

        self.assertEqual(
            structure["rows"],
            [["Year ended 2025", ""], ["Revenue", "$ 100"]],
        )
        self.assertEqual(structure["raw_cells"][0]["colspan"], 2)
        self.assertEqual(structure["source_column_indexes"], [0, 1])
        self.assertEqual(
            classify_table(table, structure, "Item 8 — Financial Statements")[0],
            "data",
        )

    def test_table_of_contents_is_navigation(self):
        table = parse_table(
            """
            <table>
              <tr><td></td><td>Page</td></tr>
              <tr><td>Item 1. Business</td><td>3</td></tr>
              <tr><td>Item 1A. Risk Factors</td><td>12</td></tr>
              <tr><td>Item 7. Management Discussion</td><td>40</td></tr>
            </table>
            """
        )

        structure = extract_table_structure(table)

        self.assertEqual(classify_table(table, structure, "Cover")[0], "navigation")

    def test_bullet_table_becomes_list_item(self):
        table = parse_table(
            """
            <table><tr><td></td><td>●</td><td>Financial risk.</td></tr></table>
            """
        )
        context = ExtractionContext(ticker="MBLY", filing_year=2025)

        blocks = emit_table(table, context)

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["content_type"], "list_item")
        self.assertEqual(blocks[0]["table_class"], "list")
        self.assertEqual(blocks[0]["text"], "Financial risk.")

    def test_exhibit_index_is_text(self):
        table = parse_table(
            """
            <table>
              <tr><td>Exhibit No.</td><td>Description</td></tr>
              <tr><td>10.1</td><td>Employment Agreement</td></tr>
              <tr><td>10.2</td><td>Purchase Agreement</td></tr>
            </table>
            """
        )

        structure = extract_table_structure(table)

        context = ExtractionContext(
            ticker="MBLY",
            filing_year=2025,
            section="Item 15 — Exhibits",
        )
        block = emit_table(table, context)

        self.assertEqual(classify_table(table, structure, context.section)[0], "text")
        self.assertEqual(block["content_type"], "text_table")
        self.assertEqual(block["table_class"], "text")

    def test_ambiguous_mixed_table_remains_unknown(self):
        table = parse_table("<table><tr><td>Ratio</td><td>1</td></tr></table>")
        structure = extract_table_structure(table)

        self.assertEqual(classify_table(table, structure, "Item 1 — Business")[0], "unknown")

    def test_financial_table_retains_title_units_headers_and_raw_cells(self):
        document = lxml_html.fromstring(
            """
            <html><body>
              <p><b>CONSOLIDATED BALANCE SHEETS</b></p>
              <table>
                <tr><th>U.S. dollars in millions</th><th>2025</th><th>2024</th></tr>
                <tr><td>Cash and cash equivalents</td><td>$ 100</td><td>$ 80</td></tr>
                <tr><td>Total assets</td><td>$ 500</td><td>$ 450</td></tr>
              </table>
            </body></html>
            """
        )
        table = document.xpath("//table")[0]
        context = ExtractionContext(
            ticker="MBLY",
            filing_year=2025,
            section="Item 8 — Financial Statements",
        )

        block = emit_table(table, context)

        self.assertEqual(block["content_type"], "data_table")
        self.assertEqual(block["table_class"], "data")
        self.assertEqual(block["title"], "CONSOLIDATED BALANCE SHEETS")
        self.assertEqual(block["units"], "U.S. dollars in millions")
        self.assertEqual(block["header_row_indexes"], [0])
        self.assertEqual(len(block["data_rows"]), 2)
        self.assertTrue(block["raw_cells"])


if __name__ == "__main__":
    unittest.main()
