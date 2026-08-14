import sys
import unittest
from pathlib import Path

from lxml import html as lxml_html


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.filings.table_processing import (
    analyze_cell_lexically,
    classify_table,
    extract_table_structure,
    link_table_continuation,
    refine_cell_kind,
    validate_markdown,
)
from src.filings.block_extraction import (
    ExtractionContext,
    append_block,
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
            "unknown",
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
              <tr><td>10.1</td><td>Form of 4.500% Notes due 2030</td></tr>
              <tr><td>10.2</td><td>Form of 5.000% Notes due 2035</td></tr>
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
        self.assertEqual(block["table_kind"], "exhibit_list")
        self.assertEqual(block["document_region"], "exhibits")
        self.assertEqual(block["logical_column_units"], ["text", "text"])

    def test_exhibit_caption_survives_content_driven_region_transition(self):
        document = lxml_html.fromstring(
            """
            <html><body>
              <p>Documents filed as part of this report are as follows:</p>
              <table>
                <tr><td>Exhibit No.</td><td>Description</td></tr>
                <tr><td>10.1</td><td>Employment Agreement</td></tr>
                <tr><td>10.2</td><td>Purchase Agreement</td></tr>
              </table>
            </body></html>
            """
        )
        block = emit_table(
            document.xpath("//table")[0],
            ExtractionContext(
                ticker="AUR",
                filing_year=2025,
                section="Item 15 — Exhibits and Financial Statement Schedules",
            ),
        )

        self.assertEqual(block["document_region"], "exhibits")
        self.assertEqual(
            block["title"], "Documents filed as part of this report are as follows"
        )
        self.assertEqual(block["title_source"], "prose_caption")

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
        self.assertEqual(
            block["logical_column_units"],
            ["text", "usd_millions", "usd_millions"],
        )
        self.assertEqual(block["header_row_indexes"], [0])
        self.assertEqual(len(block["data_rows"]), 2)
        self.assertTrue(block["raw_cells"])
        self.assertTrue(validate_markdown(block["text"]))

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
        self.assertEqual(block["logical_rows"][0], ["2026", "$20"])
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
        self.assertEqual(
            block["title"],
            "The RSUs activity for the years ended December 27, 2025 was as follows",
        )

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
        expected_title = (
            "The following tables summarize the Company's marketable debt securities"
        )
        self.assertEqual(first["title"], expected_title)
        self.assertEqual(second["title"], expected_title)
        self.assertEqual(first["logical_table_id"], second["logical_table_id"])
        self.assertTrue(second["is_continuation"])
        self.assertTrue(validate_markdown(second["text"]))

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
        self.assertEqual(block["logical_width"], 3)
        self.assertEqual(block["logical_column_units"], ["text", "usd", "percent"])
        self.assertEqual(block["logical_rows"][0][1:], ["$(377)", "21.0%"])

    def test_section_heading_can_title_adjacent_related_tables(self):
        document = lxml_html.fromstring(
            """
            <html><body>
              <table>
                <tr><th>Line item</th><th>2025</th></tr>
                <tr><td>Depreciation expense</td><td>$2,522</td></tr>
              </table>
              <table>
                <tr><th>Line item</th><th>2026</th><th>2027</th></tr>
                <tr><td>Operating lease payments</td><td>$4,541</td><td>$3,181</td></tr>
              </table>
            </body></html>
            """
        )
        context = ExtractionContext(
            ticker="F",
            filing_year=2025,
            section="Item 16 — Form 10-K Summary",
            document_region="financial_statement_notes",
        )
        heading = append_block(
            context,
            content_type="heading",
            text="Ford Credit Segment",
            source_tag="div",
            source_anchor=None,
        )

        first, second = [emit_table(table, context) for table in document.xpath("//table")]

        self.assertEqual(first["title"], "Ford Credit Segment")
        self.assertEqual(second["title"], "Ford Credit Segment")
        self.assertEqual(second["title_source_block_id"], heading["block_id"])
        self.assertNotEqual(first["logical_table_id"], second["logical_table_id"])

    def test_generic_notes_continued_heading_is_rejected_as_a_title(self):
        document = lxml_html.fromstring(
            """
            <html><body>
              <p><b>NOTES TO CONSOLIDATED FINANCIAL STATEMENTS – (Continued)</b></p>
              <table>
                <tr><th>Account</th><th>2025</th></tr>
                <tr><td>Revenue</td><td>$100</td></tr>
                <tr><td>Income</td><td>$20</td></tr>
              </table>
            </body></html>
            """
        )
        block = emit_table(
            document.xpath("//table")[0],
            ExtractionContext(
                ticker="GM",
                filing_year=2025,
                company="General Motors Company",
                section="Item 8 — Financial Statements",
                document_region="financial_statement_notes",
            ),
        )

        self.assertIsNone(block["title"])
        self.assertEqual(block["title_source"], "none")
        self.assertTrue(
            any(
                "generic_notes_header" in candidate["reason_codes"]
                for candidate in block["rejected_title_candidates"]
            )
        )

    def test_present_forecast_is_not_mistaken_for_a_caption_cue(self):
        document = lxml_html.fromstring(
            """
            <html><body>
              <p>Our present forecast may change as market conditions evolve.</p>
              <table>
                <tr><td>Measure</td><td>Value</td></tr>
                <tr><td>Committed purchases</td><td>$100</td></tr>
              </table>
            </body></html>
            """
        )
        block = emit_table(
            document.xpath("//table")[0],
            ExtractionContext(ticker="F", filing_year=2025),
        )

        self.assertIsNone(block["title"])
        self.assertFalse(
            any(
                candidate["source"] == "prose_caption"
                for candidate in block["rejected_title_candidates"]
            )
        )

    def test_caption_selection_stops_at_the_end_of_the_matching_sentence(self):
        document = lxml_html.fromstring(
            """
            <html><body>
              <p>The following table summarizes the statutory tax-rate bridge. This later sentence is background and must not become the title.</p>
              <table>
                <tr><th>Tax item</th><th>Rate</th></tr>
                <tr><td>Federal statutory rate</td><td>21%</td></tr>
                <tr><td>Effective tax rate</td><td>18%</td></tr>
              </table>
            </body></html>
            """
        )
        block = emit_table(
            document.xpath("//table")[0],
            ExtractionContext(ticker="QCOM", filing_year=2025),
        )

        self.assertEqual(
            block["title"],
            "The following table summarizes the statutory tax-rate bridge.",
        )

    def test_reviewed_long_single_sentence_caption_remains_bounded_and_accepted(self):
        caption = (
            "Revenues recognized from performance obligations satisfied in prior "
            "periods include sales-based royalties, customer incentives, software "
            "arrangements, settlement adjustments, estimates supplied by licensees, "
            "and other amounts associated with previously delivered products; these "
            "amounts reflect several distinct contract and reporting mechanisms "
            "whose effects vary by customer and reporting period, and the filing "
            "therefore provides the comparative values and corresponding fiscal-year "
            "context in one schedule, and were as follows (in millions)"
        )
        self.assertGreater(len(caption), 500)
        self.assertLessEqual(len(caption), 600)
        document = lxml_html.fromstring(
            f"""
            <html><body><p>{caption}</p>
              <table>
                <tr><th>Revenue source</th><th>2025</th></tr>
                <tr><td>Previously satisfied obligations</td><td>$783</td></tr>
                <tr><td>Other</td><td>$20</td></tr>
              </table>
            </body></html>
            """
        )
        block = emit_table(
            document.xpath("//table")[0],
            ExtractionContext(ticker="QCOM", filing_year=2025),
        )

        self.assertEqual(block["title"], caption)
        self.assertEqual(block["title_source"], "prose_caption")

    def test_typed_financial_values_and_identifiers(self):
        expectations = {
            "(17)": "numeric_scalar",
            "$10.3-11.7": "numeric_range",
            "2.39% - 5.12%": "percentage_range",
            "2027 - 2053": "year_range",
            "3,984*": "numeric_scalar",
            "12/10/2025": "date",
            "001-39463": "exhibit_or_file_identifier",
            "—": "missing_numeric",
        }
        for value, kind in expectations.items():
            with self.subTest(value=value):
                self.assertEqual(
                    analyze_cell_lexically(value)["refined_kind"],
                    kind,
                )

        footnoted = analyze_cell_lexically("3,984(1)")
        self.assertEqual(footnoted["refined_kind"], "numeric_scalar")
        self.assertEqual(footnoted["footnote_suffix"], "(1)")

    def test_typed_value_contract_covers_markers_ranges_dates_and_durations(self):
        expectations = {
            "": "empty",
            "—": "missing_numeric",
            "–": "missing_numeric",
            "-": "missing_numeric",
            "$": "currency_marker",
            "%": "percent_marker",
            "-17": "numeric_scalar",
            "+17": "numeric_scalar",
            "(17)": "numeric_scalar",
            "(1.5)%": "percentage",
            "3,984": "numeric_scalar",
            "21.0": "numeric_scalar",
            "0.1": "numeric_scalar",
            "$10": "numeric_scalar",
            "$ 10": "numeric_scalar",
            "€10": "numeric_scalar",
            "10.3-11.7": "numeric_range",
            "10.3–11.7": "numeric_range",
            "2027 - 2053": "year_range",
            "2.39% - 5.12%": "percentage_range",
            "12/10/2025": "date",
            "2025-12-10": "date",
            "December 10, 2025": "date",
            "3 years": "duration",
            "3 to 15 years": "duration",
            "4–7 years": "duration",
            "*": "footnote_marker",
            "001-39463": "exhibit_or_file_identifier",
            "ordinary prose": "text",
        }
        for value, expected in expectations.items():
            with self.subTest(value=value):
                self.assertEqual(
                    analyze_cell_lexically(value)["refined_kind"], expected
                )

        accounting = analyze_cell_lexically("(17)")
        self.assertTrue(accounting["accounting_negative"])
        self.assertEqual(accounting["numeric_value"], -17.0)
        currency_range = analyze_cell_lexically("$10.3-11.7")
        self.assertEqual(currency_range["currency_code"], "USD")
        self.assertEqual(currency_range["range_start"], 10.3)
        self.assertEqual(currency_range["range_end"], 11.7)
        duration = analyze_cell_lexically("3 to 15 years")
        self.assertEqual(duration["range_start"], 3.0)
        self.assertEqual(duration["range_end"], 15.0)

        ambiguous_exhibit = analyze_cell_lexically("10.24*")
        self.assertIn("numeric_scalar", ambiguous_exhibit["candidate_kinds"])
        self.assertIn(
            "exhibit_or_file_identifier", ambiguous_exhibit["candidate_kinds"]
        )
        page = refine_cell_kind(
            analyze_cell_lexically("59"), header_tokens="Page"
        )
        self.assertEqual(page["refined_kind"], "exhibit_or_file_identifier")
        self.assertIn(
            "footnote_marker",
            analyze_cell_lexically("(1)")["candidate_kinds"],
        )
        symmetric = analyze_cell_lexically("+/- 100 bps")
        self.assertEqual(symmetric["refined_kind"], "numeric_range")
        self.assertEqual(symmetric["range_start"], -100.0)
        self.assertEqual(symmetric["range_end"], 100.0)
        self.assertEqual(symmetric["scale"], "basis_points")
        paired = analyze_cell_lexically("$225/$(225)")
        self.assertEqual(paired["refined_kind"], "numeric_range")
        self.assertEqual(paired["range_start"], 225.0)
        self.assertEqual(paired["range_end"], -225.0)

    def test_headerless_record_grids_receive_structured_or_financial_kinds(self):
        officers = parse_table(
            """
            <table>
              <tr><td>Name</td><td>Age</td><td>Position</td></tr>
              <tr><td>Ada Example</td><td>48</td><td>Chief Executive Officer</td></tr>
              <tr><td>Ben Example</td><td>52</td><td>Chief Financial Officer</td></tr>
            </table>
            """
        )
        officer_block = emit_table(
            officers,
            ExtractionContext(ticker="OUST", filing_year=2025),
        )
        self.assertEqual(officer_block["header_mode"], "headerless")
        self.assertEqual(officer_block["table_kind"], "structured_text")
        self.assertEqual(officer_block["logical_rows"][0][0], "Name")

        sensitivity = parse_table(
            """
            <table>
              <tr><td>Assumption</td><td>Basis Point Change</td><td>Impact</td></tr>
              <tr><td>Probability of default</td><td>+/- 100 bps</td><td>$225/$(225)</td></tr>
              <tr><td>Loss given default</td><td>+/- 100</td><td>15/(15)</td></tr>
            </table>
            """
        )
        sensitivity_block = emit_table(
            sensitivity,
            ExtractionContext(
                ticker="F",
                filing_year=2025,
                section="Item 7 — Management's Discussion and Analysis",
            ),
        )
        self.assertEqual(sensitivity_block["table_kind"], "financial_data")

        useful_lives = parse_table(
            """
            <table>
              <tr><td>Machinery and equipment</td><td>3 to 15 years</td></tr>
              <tr><td>Tooling</td><td>4 to 7 years</td></tr>
            </table>
            """
        )
        useful_life_block = emit_table(
            useful_lives,
            ExtractionContext(
                ticker="TSLA",
                filing_year=2025,
                document_region="financial_statement_notes",
            ),
        )
        self.assertEqual(useful_life_block["table_kind"], "structured_text")

    def test_signature_and_critical_audit_matter_layouts_are_not_unknown(self):
        signature = parse_table(
            """
            <table>
              <tr><td>/s/ Ernst &amp; Young LLP</td><td></td></tr>
              <tr><td>San Jose, California</td><td></td></tr>
              <tr><td></td><td>February 4, 2026</td></tr>
            </table>
            """
        )
        signature_block = emit_table(
            signature,
            ExtractionContext(ticker="GOOGL", filing_year=2025),
        )
        self.assertEqual(signature_block["table_kind"], "layout")

        promoted_signature = parse_table(
            """
            <table>
              <tr><td colspan="3">/s/ Ernst &amp; Young LLP</td></tr>
              <tr><td colspan="3">We have served as the Company's auditor since 2017.</td></tr>
              <tr><td colspan="3">Detroit, Michigan</td></tr>
              <tr><td colspan="3" style="text-align:right">January 27, 2026</td></tr>
            </table>
            """
        )
        promoted_signature_block = emit_table(
            promoted_signature,
            ExtractionContext(ticker="GM", filing_year=2025),
        )
        self.assertEqual(promoted_signature_block["title_source"], "internal_title_row")
        self.assertEqual(promoted_signature_block["table_kind"], "layout")

        document = lxml_html.fromstring(
            """
            <html><body><p><b>Critical Audit Matters</b></p>
              <table>
                <tr><td>Auditing the estimate required significant judgment.</td><td></td></tr>
                <tr><td>How we addressed the matter in our audit</td><td>We tested management's controls and assumptions.</td></tr>
              </table>
            </body></html>
            """
        )
        audit_block = emit_table(
            document.xpath("//table")[0],
            ExtractionContext(ticker="GM", filing_year=2025),
        )
        self.assertEqual(audit_block["table_kind"], "layout")

    def test_continuation_negative_controls_do_not_link(self):
        def fragment(
            *,
            title="Shared heading",
            kind="financial_data",
            headers=None,
            context=None,
            rows=None,
            roles=None,
            units=None,
            region="financial_statement_notes",
        ):
            headers = headers or [["Line item"], ["Amount"]]
            rows = rows or [["Revenue", "$100"]]
            roles = roles or ["data"] * len(rows)
            units = units or ["text", "usd_millions"]
            return {
                "title": title,
                "table_kind": kind,
                "section": "Item 15 — Exhibits and Financial Statement Schedules",
                "document_region": region,
                "logical_width": 2,
                "logical_columns": [
                    {"role": "row_label"},
                    {"role": "value"},
                ],
                "logical_rows": rows,
                "logical_row_roles": roles,
                "logical_header_paths": headers,
                "logical_header_context": context or [],
                "logical_column_units": units,
                "native_context": {"missing_fields": []},
                "continuation_cues": [],
            }

        previous = fragment(headers=[["Account"], ["2025"]])
        unrelated_same_width = fragment(headers=[["Name"], ["Date"]])
        decision = link_table_continuation(
            unrelated_same_width, previous, intervening_meaningful_blocks=0
        )
        self.assertFalse(decision["accepted"])
        self.assertEqual(decision["rejection_reasons"], ["no_strong_positive_signal"])

        period_previous = fragment(
            title="First explicit caption",
            context=["December 31, 2025"],
        )
        conflicting_caption = fragment(
            title="Second explicit caption",
            context=["December 31, 2024"],
        )
        decision = link_table_continuation(
            conflicting_caption, period_previous, intervening_meaningful_blocks=0
        )
        self.assertFalse(decision["accepted"])
        self.assertIn("conflicting_explicit_title", decision["rejection_reasons"])

        exhibit = fragment(kind="exhibit_list", region="exhibits")
        signature = fragment(kind="layout", region="signatures")
        decision = link_table_continuation(
            signature, exhibit, intervening_meaningful_blocks=0
        )
        self.assertFalse(decision["accepted"])
        self.assertIn("table_kind_family_conflict", decision["rejection_reasons"])

        section_label_only = fragment(
            rows=[["North America", ""]],
            roles=["section_label"],
        )
        decision = link_table_continuation(
            section_label_only, previous, intervening_meaningful_blocks=0
        )
        self.assertFalse(decision["accepted"])

        decision = link_table_continuation(
            fragment(rows=[["Debt", "$40"]]),
            previous,
            intervening_meaningful_blocks=1,
        )
        self.assertFalse(decision["accepted"])
        self.assertIn("intervening_semantic_content", decision["rejection_reasons"])

        decision = link_table_continuation(
            fragment(units=["text", "percent"]),
            previous,
            intervening_meaningful_blocks=0,
        )
        self.assertFalse(decision["accepted"])
        self.assertIn("native_unit_conflict", decision["rejection_reasons"])

        discontinued = fragment(title="Discontinued Operations")
        decision = link_table_continuation(
            discontinued,
            fragment(title="Discontinued Operations"),
            intervening_meaningful_blocks=0,
        )
        self.assertFalse(decision["accepted"])
        self.assertNotIn("explicit_continued_cue", decision["reasons"])

        explicit = fragment(title="Shared heading")
        explicit["continuation_cues"] = ["(Continued)"]
        decision = link_table_continuation(
            explicit,
            fragment(title="Shared heading"),
            intervening_meaningful_blocks=0,
        )
        self.assertTrue(decision["accepted"])
        self.assertIn("explicit_continued_cue", decision["reasons"])

        low_confidence_units = fragment(
            title="Shared heading (Continued)",
            units=["text", "mixed"],
        )
        decision = link_table_continuation(
            low_confidence_units,
            fragment(title="Shared heading", units=["text", "usd_millions"]),
            intervening_meaningful_blocks=0,
        )
        self.assertTrue(decision["accepted"])
        self.assertNotIn("native_unit_conflict", decision["rejection_reasons"])


if __name__ == "__main__":
    unittest.main()
