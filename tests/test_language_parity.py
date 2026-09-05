import asyncio
import unittest
from unittest.mock import patch

from src.evaluation.language_parity import _substantive_numbers, evaluate_pairs, load_pairs


class LanguageParityTests(unittest.TestCase):
    def test_manifest_selects_ten_reviewed_pairs(self):
        self.assertEqual(len(load_pairs()), 10)

    def test_substantive_numbers_excludes_list_ordinals_and_years(self):
        self.assertEqual(
            _substantive_numbers("1. First\n2. Second\nAptiv operates in 50 countries as of 2025."),
            ["50"],
        )

    def test_pair_scoring_compares_routes_evidence_numbers_and_citations(self):
        pair = load_pairs()[0]

        async def execute(_pipeline, _query, _language, scope):
            return {
                "answer": "answer", "route": "filing", "resolved_tickers": list(scope),
                "final_evidence_ids": ["AUR-2025-CHUNK-000002"],
                "citation_ids": ["AUR-2025-CHUNK-000002"], "numbers": [],
                "safe_error_class": None,
            }

        with patch("src.evaluation.language_parity._execute", execute):
            result = asyncio.run(evaluate_pairs([pair], object()))

        self.assertEqual(result["pair_count"], 1)
        self.assertEqual(result["summary"]["company_resolution_match"], 1.0)
        self.assertEqual(result["summary"]["citation_ids_match"], 1.0)


if __name__ == "__main__":
    unittest.main()
