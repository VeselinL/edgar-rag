import unittest

from src.backend.sources import SourceNormalizationError, normalize_source, normalize_sources


COMMON = {
    "company": "Mobileye Global Inc.",
    "ticker": "MBLY",
    "filing_year": 2025,
    "section": "Item 8 — Financial Statements",
    "source_url": "https://www.sec.gov/example",
}


class SourceNormalizationTests(unittest.TestCase):
    def test_narrative_source(self):
        source = normalize_source({**COMMON, "content_type": "narrative", "text": "Complete text."})
        self.assertEqual(source["content_type"], "text")
        self.assertEqual(source["text"], "Complete text.")
        self.assertNotIn("chunk_id", source)

    def test_structured_table_source(self):
        source = normalize_source(
            {
                **COMMON,
                "content_type": "table",
                "title": "Revenue",
                "units": "USD millions",
                "logical_column_headers": ["Category", "2025", "2024"],
                "logical_rows": [["Product", "10", ""], ["Other", "2", "3"]],
                "logical_column_units": ["text", "USD millions", "USD millions"],
                "text": "| Markdown is not parsed |",
            }
        )
        self.assertEqual(source["headers"], ["Category", "2025", "2024"])
        self.assertEqual(source["rows"][0][-1], "")
        self.assertNotIn("text", source)

    def test_web_source_keeps_public_provenance_without_internal_id(self):
        source = normalize_source(
            {
                "chunk_id": "web-1",
                "content_type": "web",
                "title": "Current report",
                "publisher": "example.com",
                "retrieved_at": "2026-09-01T00:00:00+00:00",
                "source_url": "https://example.com/report",
                "text": "Bounded search excerpt.",
            }
        )
        self.assertEqual(source["content_type"], "web")
        self.assertEqual(source["excerpt"], "Bounded search excerpt.")
        self.assertNotIn("chunk_id", source)

    def test_malformed_table_is_not_reconstructed_from_markdown(self):
        with self.assertRaises(SourceNormalizationError):
            normalize_source(
                {
                    **COMMON,
                    "content_type": "table",
                    "logical_column_headers": [],
                    "logical_rows": [],
                    "text": "| A | B |",
                }
            )

    def test_malformed_sibling_does_not_hide_valid_source(self):
        sources, malformed = normalize_sources(
            [
                {**COMMON, "content_type": "narrative", "text": "Valid."},
                {**COMMON, "content_type": "table", "logical_rows": []},
            ]
        )
        self.assertEqual(len(sources), 1)
        self.assertEqual(malformed, 1)


if __name__ == "__main__":
    unittest.main()
