import sys
import unittest
from pathlib import Path

from lxml import html as lxml_html


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from preprocess_filing import ExtractionContext, emit_paragraph


def parse_paragraph(markup: str):
    return lxml_html.fromstring(markup)


class HeadingProcessingTests(unittest.TestCase):
    def test_fully_bold_standalone_paragraph_becomes_subheading(self):
        context = ExtractionContext(
            ticker="MBLY",
            filing_year=2025,
            section="Item 1 — Business",
        )

        block = emit_paragraph(
            parse_paragraph("<p><b>Company Overview</b></p>"),
            context,
        )

        self.assertEqual(block["content_type"], "heading")
        self.assertEqual(block["heading_kind"], "subsection")
        self.assertEqual(block["heading_level"], 2)
        self.assertEqual(
            block["section_path"],
            ["Item 1 — Business", "Company Overview"],
        )

    def test_partial_bold_emphasis_remains_paragraph(self):
        context = ExtractionContext(
            ticker="MBLY",
            filing_year=2025,
            section="Item 1 — Business",
        )

        block = emit_paragraph(
            parse_paragraph("<p><b>Mobileye</b> develops driving technology.</p>"),
            context,
        )

        self.assertEqual(block["content_type"], "paragraph")
        self.assertNotIn("heading_kind", block)

    def test_item_heading_has_item_metadata(self):
        context = ExtractionContext(ticker="MBLY", filing_year=2025)

        block = emit_paragraph(
            parse_paragraph("<p><b>Item 1. Business</b></p>"),
            context,
        )

        self.assertEqual(block["content_type"], "heading")
        self.assertEqual(block["heading_kind"], "item")
        self.assertEqual(block["heading_level"], 1)
        self.assertEqual(block["section_path"], ["Item 1 — Business"])


if __name__ == "__main__":
    unittest.main()
