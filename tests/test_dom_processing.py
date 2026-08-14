import unittest

from lxml import html as lxml_html

from src.filings.dom_processing import collect_visible_text


class VisibleTextTests(unittest.TestCase):
    def test_br_adds_a_word_boundary(self):
        node = lxml_html.fromstring("<p>of<br>contractual</p>")
        self.assertEqual(collect_visible_text(node), "of contractual")

    def test_hyphenated_br_joins_without_space(self):
        node = lxml_html.fromstring(
            "<p>Commodity<br>pass-<br>through</p>"
        )
        self.assertEqual(collect_visible_text(node), "Commodity pass-through")

    def test_soft_hyphen_at_boundary_is_removed(self):
        node = lxml_html.fromstring("<p>inter\u00ad<br>national</p>")
        self.assertEqual(collect_visible_text(node), "international")

    def test_inline_formatting_does_not_split_words(self):
        node = lxml_html.fromstring(
            "<p>inter<span>national</span> and <b>Net</b> income</p>"
        )
        self.assertEqual(collect_visible_text(node), "international and Net income")

    def test_nested_table_text_can_be_excluded(self):
        node = lxml_html.fromstring(
            "<td>Outer<table><tr><td>Nested</td></tr></table> tail</td>"
        )
        self.assertEqual(
            collect_visible_text(node, excluded_descendant_tags={"table"}),
            "Outer tail",
        )


if __name__ == "__main__":
    unittest.main()
