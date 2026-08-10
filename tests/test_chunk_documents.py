import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.chunking.chunk_documents import chunk_blocks


CONFIG = {
    "chunk_size": 180,
    "chunk_overlap": 30,
    "length_function": "characters",
    "separators": ["\n\n", "\n", ". ", " ", ""],
    "excluded_content_types": ["navigation"],
    "narrative_content_types": ["heading", "paragraph", "list_item"],
    "table_content_types": ["data_table", "text_table", "unknown_table"],
}


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
        "content_type": content_type,
        "text": text,
        "source_anchor": f"anchor-{index}",
        "page_start": None,
        "page_end": None,
        "source_url": "https://example.com/filing.htm",
    }


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
        self.assertTrue(all("evidence" in chunk["text"] for chunk in chunks))

    def test_oversized_paragraph_is_split_with_source_block_preserved(self):
        paragraph = block(1, "paragraph", "Evidence sentence. " * 30)

        chunks = chunk_blocks([paragraph], CONFIG)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk["text"]) <= CONFIG["chunk_size"] for chunk in chunks))
        self.assertTrue(all(chunk["block_ids"] == [paragraph["block_id"]] for chunk in chunks))

    def test_complete_table_is_one_chunk_with_context(self):
        table = block(1, "data_table", "unused", "Item 8")
        table.update(
            {
                "table_class": "data",
                "title": "Balance Sheets",
                "units": "mixed",
                "column_units": [None, "dollars", "percent"],
                "rows": [
                    ["Line item", "2025", "2024"],
                    ["Cash", "100", "80"],
                    ["Assets", "500", "450"],
                    ["Liabilities", "200", "190"],
                ],
                "header_row_indexes": [0],
            }
        )

        table_config = {**CONFIG, "chunk_size": 100}
        chunks = chunk_blocks([table], table_config)

        self.assertEqual(len(chunks), 1)
        chunk = chunks[0]
        self.assertEqual(chunk["content_type"], "table")
        self.assertEqual(chunk["table_row_indexes"], [1, 2, 3])
        self.assertEqual(chunk["table_rows"], table["rows"][1:])
        self.assertIn("Balance Sheets", chunk["text"])
        self.assertIn("Units: mixed", chunk["text"])
        self.assertIn("Column units:  | dollars | percent", chunk["text"])
        self.assertIn("Line item | 2025 | 2024", chunk["text"])
        self.assertIn("Liabilities | 200 | 190", chunk["text"])
        self.assertGreater(len(chunk["text"]), table_config["chunk_size"])

    def test_navigation_is_excluded(self):
        navigation = block(1, "navigation", "Table of contents")
        paragraph = block(2, "paragraph", "Useful evidence.")

        chunks = chunk_blocks([navigation, paragraph], CONFIG)

        self.assertEqual(len(chunks), 1)
        self.assertNotIn("Table of contents", chunks[0]["text"])

    def test_fixed_strategy_respects_size_and_overlap(self):
        config = {**CONFIG, "strategy": "fixed", "chunk_size": 100, "chunk_overlap": 20}
        paragraph = block(1, "paragraph", "0123456789" * 30)

        chunks = chunk_blocks([paragraph], config)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk["text"]) <= config["chunk_size"] for chunk in chunks))
        self.assertTrue(all(chunk["source_text_start"] < chunk["source_text_end"] for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
