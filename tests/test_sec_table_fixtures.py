import hashlib
import json
import sys
import unittest
from collections import Counter, defaultdict
from pathlib import Path

from lxml import html as lxml_html


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.chunking.chunk_documents import chunk_table
from src.filings.audit_tables import _distribution, _title_quality_flags
from src.filings.block_extraction import extract_blocks
from src.filings.dom_processing import (
    drop_hidden_nodes,
    drop_non_text_nodes,
    drop_page_furniture,
    drop_xbrl_tags,
)
from src.filings.fetch_data import COMPANIES
from src.filings.filing_io import load_extraction_metadata, parse_filing_html
from src.filings.preprocess_filing import validate_blocks
from src.filings.table_processing import (
    TABLE_HEURISTICS_VERSION,
    TABLE_SCHEMA_VERSION,
    extract_table_structure,
    table_fingerprint,
    table_quality_metrics,
)


MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "tables" / "manifest.json"
CORPUS_BASELINE_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "tables" / "corpus-quality-baseline.json"
)


def sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def clean(document) -> None:
    drop_non_text_nodes(document)
    drop_hidden_nodes(document)
    drop_xbrl_tags(document)
    drop_page_furniture(document)


class SecTableFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.raw_by_ticker = {
            value["ticker"]: value for value in cls.manifest["raw_filings"]
        }

    def test_fixture_slices_are_exact_and_independently_parseable(self):
        records = list(self.manifest["priority_fixtures"])
        for group in self.manifest["positive_continuations"]:
            for fixture in group["fixtures"]:
                records.append(
                    {
                        "id": group["id"],
                        "ticker": group["ticker"],
                        "fixture": fixture,
                        "context_fixture": group["context_fixture"],
                    }
                )

        checked_paths = set()
        for record in records:
            raw = (PROJECT_ROOT / self.raw_by_ticker[record["ticker"]]["filing"]).read_bytes()
            for key in ("fixture", "context_fixture"):
                fixture = record.get(key)
                if not fixture or fixture["path"] in checked_paths:
                    continue
                checked_paths.add(fixture["path"])
                value = (PROJECT_ROOT / fixture["path"]).read_bytes()
                with self.subTest(record=record["id"], fixture=fixture["path"]):
                    self.assertEqual(sha256(value), fixture["sha256"])
                    self.assertEqual(
                        value,
                        raw[fixture["raw_byte_start"] : fixture["raw_byte_end"]],
                    )
                    parsed = lxml_html.fragment_fromstring(value, create_parent="div")
                    self.assertTrue(parsed.xpath(".//table"))

    def test_isolated_priority_fixtures_preserve_physical_source_evidence(self):
        for record in self.manifest["priority_fixtures"]:
            fixture_path = PROJECT_ROOT / record["fixture"]["path"]
            document = lxml_html.fragment_fromstring(
                fixture_path.read_bytes(), create_parent="div"
            )
            clean(document)
            [table] = document.xpath(".//table")
            html_table_id = (
                f"{record['ticker']}-{record['filing_year']}-HTMLTABLE-"
                f"{record['html_table_index_one_based']:04d}"
            )
            structure = extract_table_structure(table, html_table_id)
            raw_ids = [cell["raw_cell_id"] for cell in structure["raw_cells"]]
            with self.subTest(fixture=record["id"]):
                self.assertEqual(
                    len(structure["physical_rows"][0]),
                    record["expected_physical_display_width"],
                )
                self.assertEqual(
                    table_fingerprint(table), record["source_fragment_fingerprint"]
                )
                self.assertEqual(len(raw_ids), len(set(raw_ids)))
                self.assertTrue(
                    all(
                        cell["physical_start"] < cell["physical_end"]
                        for cell in structure["raw_cells"]
                    )
                )


class SlowSecCorpusFixtureAudit(unittest.TestCase):
    """Cross-company golden gate over the ten immutable local SEC filings."""

    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        company_key_by_ticker = {
            value["ticker"]: key for key, value in COMPANIES.items()
        }
        cls.blocks_by_ticker = {}
        cls.table_by_ticker_and_index = {}
        for raw_record in cls.manifest["raw_filings"]:
            ticker = raw_record["ticker"]
            filing_path = PROJECT_ROOT / raw_record["filing"]
            raw = filing_path.read_bytes()
            if sha256(raw) != raw_record["raw_sha256"]:
                raise AssertionError(f"Frozen raw hash changed for {ticker}")
            document = parse_filing_html(raw)
            metadata = load_extraction_metadata(
                filing_path,
                document,
                COMPANIES[company_key_by_ticker[ticker]],
                raw_record["filing_year"],
            )
            clean(document)
            blocks = extract_blocks(
                document,
                ticker,
                raw_record["filing_year"],
                metadata=metadata,
            )
            validate_blocks(blocks)
            cls.blocks_by_ticker[ticker] = {
                block["block_id"]: block for block in blocks
            }
            cls.table_by_ticker_and_index[ticker] = {
                block["html_table_index"]: block
                for block in blocks
                if "html_table_index" in block
            }

    def test_priority_golden_contracts(self):
        for record in self.manifest["priority_fixtures"]:
            block = self.table_by_ticker_and_index[record["ticker"]][
                record["html_table_index_one_based"]
            ]
            expected = record["assertions"]
            values = [value for row in block["logical_rows"] for value in row]
            metrics = table_quality_metrics(block)
            with self.subTest(fixture=record["id"]):
                self.assertEqual(block["block_id"], record["historical_block_id"])
                self.assertEqual(block["table_schema_version"], TABLE_SCHEMA_VERSION)
                self.assertEqual(
                    block["table_heuristics_version"], TABLE_HEURISTICS_VERSION
                )
                self.assertEqual(block["html_table_xpath"], record["cleaned_xpath"])
                self.assertEqual(
                    block["html_table_fingerprint"],
                    record["source_fragment_fingerprint"],
                )
                self.assertEqual(
                    block["normalization_diagnostics"]["physical_display_width"],
                    record["expected_physical_display_width"],
                )
                self.assertEqual(block["logical_width"], record["expected_logical_width"])
                self.assertEqual(metrics["raw_cell_accounting_coverage"], 1.0)
                self.assertEqual(metrics["standalone_marker_count"], 0)
                self.assertTrue(metrics["markdown_valid"])
                for field in (
                    "table_kind",
                    "header_mode",
                    "title",
                    "document_region",
                    "is_continuation",
                    "continued_from_block_id",
                    "header_row_source_indexes",
                    "header_row_indexes",
                    "logical_column_headers",
                    "logical_column_units",
                ):
                    if field in expected:
                        self.assertEqual(block.get(field), expected[field])
                if "header_contains" in expected:
                    header_text = " ".join(block["logical_column_headers"])
                    for value in expected["header_contains"]:
                        self.assertIn(value, header_text)
                for key in ("row_exact", "body_row_exact", "first_row"):
                    if key in expected:
                        self.assertIn(expected[key], block["logical_rows"])
                if "row_contains" in expected:
                    self.assertTrue(
                        any(
                            all(value in row for value in expected["row_contains"])
                            for row in block["logical_rows"]
                        )
                    )
                if "values_include" in expected:
                    for value in expected["values_include"]:
                        self.assertIn(value, values)
                if "page_values" in expected:
                    self.assertEqual(
                        [row[-1] for row in block["logical_rows"]],
                        expected["page_values"],
                    )
                if "body_row_role" in expected:
                    row_index = block["logical_rows"].index(expected["body_row_exact"])
                    self.assertEqual(
                        block["logical_row_roles"][row_index],
                        expected["body_row_role"],
                    )
                if "shared_logical_table_with" in expected:
                    other = self.blocks_by_ticker[record["ticker"]][
                        expected["shared_logical_table_with"]
                    ]
                    self.assertEqual(
                        block["logical_table_id"], other["logical_table_id"]
                    )

    def test_positive_continuations_link_and_compose_once(self):
        for record in self.manifest["positive_continuations"]:
            blocks = [
                self.blocks_by_ticker[record["ticker"]][block_id]
                for block_id in record["historical_block_ids"]
            ]
            with self.subTest(continuation=record["id"]):
                self.assertEqual(len({b["logical_table_id"] for b in blocks}), 1)
                self.assertEqual(
                    [block["table_fragment_index"] for block in blocks],
                    list(range(1, len(blocks) + 1)),
                )
                self.assertTrue(all(block["is_continuation"] for block in blocks[1:]))
                [chunk] = chunk_table(blocks, {})
                expected_mode = record["expected_composition_mode"]
                if expected_mode == "horizontal_or_compound":
                    self.assertIn(chunk["composition_mode"], {"horizontal", "compound"})
                else:
                    self.assertEqual(chunk["composition_mode"], expected_mode)
                self.assertEqual(
                    chunk["fragment_block_ids"], record["historical_block_ids"]
                )

    def test_reviewed_corpus_continuation_repairs(self):
        reviewed = (
            (
                "APTV",
                ["APTV-2025-000743", "APTV-2025-000744"],
                "compound",
            ),
            (
                "QCOM",
                ["QCOM-2025-000522", "QCOM-2025-000523"],
                "vertical",
            ),
        )
        for ticker, block_ids, expected_mode in reviewed:
            blocks = [self.blocks_by_ticker[ticker][block_id] for block_id in block_ids]
            with self.subTest(ticker=ticker, block_ids=block_ids):
                self.assertEqual(len({block["logical_table_id"] for block in blocks}), 1)
                self.assertTrue(blocks[1]["is_continuation"])
                self.assertEqual(blocks[1]["continued_from_block_id"], block_ids[0])
                [chunk] = chunk_table(blocks, {})
                self.assertEqual(chunk["composition_mode"], expected_mode)
                self.assertEqual(chunk["fragment_block_ids"], block_ids)

    def test_nvidia_human_labeled_region_inventory(self):
        lookup = self.blocks_by_ticker["NVDA"]
        for record in self.manifest["nvidia_financial_region_gold_inventory"]:
            block = lookup[record["historical_block_id"]]
            with self.subTest(block_id=record["historical_block_id"]):
                self.assertEqual(
                    block["html_table_index"], record["html_table_index_one_based"]
                )
                self.assertEqual(block["html_table_xpath"], record["cleaned_xpath"])
                self.assertEqual(
                    block["html_table_fingerprint"],
                    record["source_fragment_fingerprint"],
                )
                self.assertEqual(block["table_kind"], record["expected_table_kind"])

    def test_reviewed_corpus_quality_baseline(self):
        """Detect parser drift without blessing new values during the test run."""
        expected = json.loads(CORPUS_BASELINE_PATH.read_text(encoding="utf-8"))
        tables = [
            block
            for lookup in self.blocks_by_ticker.values()
            for block in lookup.values()
            if block.get("content_type")
            in {"data_table", "text_table", "unknown_table", "navigation"}
        ]
        metrics = [table_quality_metrics(block) for block in tables]
        logical_groups = defaultdict(list)
        for block in tables:
            logical_groups[block["logical_table_id"]].append(block)

        actual = {
            "schema_version": expected["schema_version"],
            "approved_on": expected["approved_on"],
            "approval_note": expected["approval_note"],
            "table_schema_version": TABLE_SCHEMA_VERSION,
            "table_heuristics_version": TABLE_HEURISTICS_VERSION,
            "table_fragment_count": len(tables),
            "logical_table_count": len(logical_groups),
            "table_kind_counts": dict(
                sorted(Counter(block["table_kind"] for block in tables).items())
            ),
            "table_class_counts": dict(
                sorted(Counter(block["table_class"] for block in tables).items())
            ),
            "maximum_widths": {
                "source_coordinate": max(
                    block["normalization_diagnostics"]["source_coordinate_width"]
                    for block in tables
                ),
                "physical_display": max(
                    block["normalization_diagnostics"]["physical_display_width"]
                    for block in tables
                ),
                "logical": max(block["logical_width"] for block in tables),
            },
            "empty_density": {
                "source_coordinate": _distribution(
                    value["source_coordinate_empty_density"] for value in metrics
                ),
                "physical_display": _distribution(
                    value["physical_display_empty_density"] for value in metrics
                ),
                "logical": _distribution(
                    value["logical_empty_density"] for value in metrics
                ),
            },
            "standalone_marker_count": sum(
                value["standalone_marker_count"] for value in metrics
            ),
            "normalization_collision_count": sum(
                len(block["normalization_diagnostics"]["collisions"])
                for block in tables
            ),
            "unmapped_nonempty_cell_count": sum(
                len(
                    block["normalization_diagnostics"][
                        "unmapped_nonempty_raw_cell_ids"
                    ]
                )
                for block in tables
            ),
            "raw_cell_accounting_min": min(
                value["raw_cell_accounting_coverage"] for value in metrics
            ),
            "title_quality_counts": dict(
                sorted(
                    Counter(
                        flag
                        for block in tables
                        for flag in _title_quality_flags(block)
                    ).items()
                )
            ),
            "continuation_link_count": sum(
                len(group) - 1 for group in logical_groups.values()
            ),
            "fixture_outcomes": {
                "priority_fixture_count": len(self.manifest["priority_fixtures"]),
                "positive_continuation_group_count": len(
                    self.manifest["positive_continuations"]
                ),
                "nvidia_gold_count": len(
                    self.manifest["nvidia_financial_region_gold_inventory"]
                ),
            },
            "by_ticker": {},
        }
        for ticker, lookup in sorted(self.blocks_by_ticker.items()):
            ticker_tables = [
                block
                for block in lookup.values()
                if block.get("content_type")
                in {"data_table", "text_table", "unknown_table", "navigation"}
            ]
            actual["by_ticker"][ticker] = {
                "table_fragment_count": len(ticker_tables),
                "logical_table_count": len(
                    {block["logical_table_id"] for block in ticker_tables}
                ),
            }

        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
