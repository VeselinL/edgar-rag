import sys
import unittest
from pathlib import Path

from lxml import html as lxml_html


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.filings.table_processing import (
    classify_table,
    extract_table_structure
)
from src.filings.block_extraction import (
    ExtractionContext,
    emit_table
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

    def test_bold_td_officer_labels_are_the_header_row(self):
        table = parse_table(
            """
            <table>
              <tr>
                <td style="border-bottom: 1px solid #000"><p><b>Name</b></p></td>
                <td style="border-bottom: 1px solid #000"><p style="text-align:center"><b>Age</b></p></td>
                <td style="border-bottom: 1px solid #000"><p><b>Position</b></p></td>
              </tr>
              <tr><td>Amnon Shashua</td><td>65</td><td>Chief Executive Officer</td></tr>
              <tr><td>Moran Shemesh Rojansky</td><td>45</td><td>Chief Financial Officer</td></tr>
            </table>
            """
        )
        block = emit_table(
            table,
            ExtractionContext(ticker="MBLY", filing_year=2025),
        )

        self.assertEqual(block["header_row_indexes"], [0])
        self.assertEqual(block["data_rows"][0][0], "Amnon Shashua")
        name_cell, age_cell = block["raw_cells"][:2]
        self.assertTrue(name_cell["is_bold"])
        self.assertTrue(name_cell["has_bottom_border"])
        self.assertEqual(age_cell["alignment"], "center")

    def test_equity_header_stops_before_opening_balance(self):
        table = parse_table(
            """
            <table>
              <tr><td></td><td colspan="2"><b>Common Stock</b></td><td><b>Total</b></td></tr>
              <tr><td></td><td><b>Number of</b></td><td><b>Additional</b></td><td><b>Shareholders'</b></td></tr>
              <tr><td><b>U.S. dollars except number of shares, in millions</b></td><td><b>shares</b></td><td><b>paid-in capital</b></td><td><b>Equity</b></td></tr>
              <tr><td><b>Balance as of December 31, 2022</b></td><td>802</td><td>14,737</td><td>14,794</td></tr>
              <tr><td>Net income (loss)</td><td>—</td><td>—</td><td>(27)</td></tr>
            </table>
            """
        )
        block = emit_table(
            table,
            ExtractionContext(
                ticker="MBLY",
                filing_year=2025,
                section="Item 8 — Financial Statements",
            ),
        )

        self.assertEqual(block["header_row_indexes"], [0, 1, 2])
        self.assertEqual(block["data_rows"][0][0], "Balance as of December 31, 2022")
        self.assertNotIn("802", " ".join(block["column_headers"]))

    def test_lease_year_rows_are_data_and_caption_is_the_title(self):
        document = lxml_html.fromstring(
            """
            <html><body>
              <p>MOBILEYE GLOBAL INC.</p>
              <p style="font-weight:bold">Operating lease expenses are recognized over the lease term.</p>
              <p>Maturities of operating lease liabilities were as follows:</p>
              <table>
                <tr><td colspan="3"><b>December 27,</b></td></tr>
                <tr><td><b>U.S. Dollars in millions</b></td><td colspan="2"><b>2025</b></td></tr>
                <tr><td>2026</td><td>$</td><td>20</td></tr>
                <tr><td>2027</td><td></td><td>17</td></tr>
                <tr><td>2028</td><td></td><td>14</td></tr>
              </table>
            </body></html>
            """
        )
        block = emit_table(
            document.xpath("//table")[0],
            ExtractionContext(
                ticker="MBLY",
                filing_year=2025,
                section="Item 8 — Financial Statements",
            ),
        )

        self.assertEqual(block["header_row_indexes"], [0, 1])
        self.assertEqual(block["data_rows"][0], ["2026", "$", "20"])
        self.assertEqual(
            block["title"],
            "Maturities of operating lease liabilities were as follows",
        )

    def test_rsu_opening_balance_is_data_and_subsection_is_the_title(self):
        document = lxml_html.fromstring(
            """
            <html><body>
              <p>MOBILEYE GLOBAL INC.</p>
              <p>Restricted Stock Units</p>
              <p>The RSUs activity for the years ended December 27, 2025 was as follows:</p>
              <table>
                <tr><td></td><td></td><td colspan="2"><b>Weighted average grant</b></td></tr>
                <tr><td></td><td><b>Number of RSUs</b></td><td colspan="2"><b>date fair value per share</b></td></tr>
                <tr><td></td><td><b>In thousands</b></td><td colspan="2"><b>U.S. dollars</b></td></tr>
                <tr><td><b>Outstanding as of December 31, 2022</b></td><td>12,564</td><td>$</td><td>21</td></tr>
                <tr><td>Granted</td><td>6,782</td><td></td><td>40</td></tr>
              </table>
            </body></html>
            """
        )
        block = emit_table(
            document.xpath("//table")[0],
            ExtractionContext(
                ticker="MBLY",
                filing_year=2025,
                section="Item 8 — Financial Statements",
            ),
        )

        self.assertEqual(block["header_row_indexes"], [0, 1, 2])
        self.assertEqual(block["data_rows"][0][0], "Outstanding as of December 31, 2022")
        self.assertEqual(block["title"], "Restricted Stock Units")

    def test_debt_investment_headers_and_title_carry_to_continuation_table(self):
        document = lxml_html.fromstring(
            """
            <html><body>
              <p><b>NOTE 13 - INVESTMENTS</b></p>
              <p>Debt Investments</p>
              <p>Debt investments include U.S. government bonds and money market funds.</p>
              <p>The following tables summarize the Company's marketable debt securities:</p>
              <table>
                <tr><td><b>U.S. dollars in millions</b></td><td colspan="4"><b>December 27, 2025</b></td></tr>
                <tr><td></td><td></td><td></td><td colspan="2"><b>Reported as</b></td></tr>
                <tr><td></td><td><b>Amortized cost</b></td><td><b>Fair value</b></td><td><b>Cash and cash equivalents</b></td><td><b>Other current assets</b></td></tr>
                <tr><td>U.S. government bonds</td><td>55</td><td>55</td><td>—</td><td>55</td></tr>
                <tr><td>Money market funds</td><td>1,016</td><td>1,016</td><td>1,016</td><td>—</td></tr>
              </table>
              <p></p>
              <table>
                <tr><td><b>U.S. dollars in millions</b></td><td colspan="4"><b>December 28, 2024</b></td></tr>
                <tr><td></td><td></td><td></td><td colspan="2"><b>Reported as</b></td></tr>
                <tr><td></td><td><b>Amortized cost</b></td><td><b>Fair value</b></td><td><b>Cash and cash equivalents</b></td><td><b>Other current assets</b></td></tr>
                <tr><td>U.S. government bonds</td><td>33</td><td>33</td><td>—</td><td>33</td></tr>
                <tr><td>Money market funds</td><td>951</td><td>951</td><td>951</td><td>—</td></tr>
              </table>
            </body></html>
            """
        )
        context = ExtractionContext(
            ticker="MBLY",
            filing_year=2025,
            section="Item 8 — Financial Statements",
        )

        first, second = [emit_table(table, context) for table in document.xpath("//table")]

        self.assertEqual(first["header_row_indexes"], [0, 1, 2])
        self.assertEqual(second["header_row_indexes"], [0, 1, 2])
        self.assertEqual(first["title"], "Debt Investments")
        self.assertEqual(second["title"], "Debt Investments")
        self.assertIn(" |  |  | Reported as | ", second["text"])

    def test_dollar_and_percent_columns_use_mixed_units(self):
        table = parse_table(
            """
            <table>
              <tr><td></td><td colspan="2"><b>December 27, 2025</b></td><td colspan="2"><b>December 28, 2024</b></td></tr>
              <tr><td></td><td><b>$</b></td><td></td><td></td><td><b>%</b></td></tr>
              <tr><td>Income (loss) before income taxes</td><td>$</td><td>(377)</td><td>21.0</td><td>%</td></tr>
              <tr><td>Foreign Rate Differential</td><td></td><td>7</td><td>1.9</td><td>%</td></tr>
            </table>
            """
        )
        block = emit_table(
            table,
            ExtractionContext(
                ticker="MBLY",
                filing_year=2025,
                section="Item 8 — Financial Statements",
            ),
        )

        self.assertEqual(block["header_row_indexes"], [0, 1])
        self.assertEqual(block["units"], "mixed")
        self.assertEqual(
            block["column_units"],
            [None, "dollars", "dollars", "percent", "percent"],
        )


if __name__ == "__main__":
    unittest.main()
