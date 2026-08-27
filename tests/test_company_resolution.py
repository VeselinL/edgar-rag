import json
import unittest
from pathlib import Path

from src.evaluation.ava_p0 import evaluate_resolution
from src.resolution.companies import (
    CompanyResolver,
    damerau_levenshtein,
    default_company_resolver,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CompanyResolverTests(unittest.TestCase):
    def setUp(self):
        self.resolver = CompanyResolver()

    def test_all_frozen_resolution_labels_pass_deterministically(self):
        payload = json.loads(
            (
                PROJECT_ROOT
                / "data/evaluation/ava_p0/v1/company_resolution_v1.json"
            ).read_text(encoding="utf-8")
        )
        failures = []
        for case in payload["cases"]:
            result = self.resolver.resolve(case["query"])
            actual = (
                list(result.resolved_tickers),
                result.scope,
                result.needs_clarification,
            )
            expected = (
                case["expected_tickers"],
                case["expected_scope"],
                case["expected_needs_clarification"],
            )
            if actual != expected:
                failures.append((case["id"], expected, actual))
        self.assertEqual(failures, [])

    def test_evaluator_uses_the_same_shared_resolution_result(self):
        case = {
            "id": "shared-frod",
            "category": "typo_transposition",
            "query": "What are frod's principal segments?",
            "expected_tickers": ["F"],
            "expected_scope": "single_company",
            "expected_needs_clarification": False,
        }
        direct = default_company_resolver.resolve(case["query"])
        evaluated = evaluate_resolution([case])["records"][0]
        self.assertEqual(evaluated["detected_tickers"], list(direct.resolved_tickers))
        self.assertEqual(evaluated["methods"], list(direct.methods))
        self.assertEqual(evaluated["confidence_bands"], ["medium"])

    def test_fuzzy_transposition_records_method_and_confidence(self):
        result = self.resolver.resolve("Compare frod and Tesal revenue.")
        self.assertEqual(result.resolved_tickers, ("TSLA", "F"))
        self.assertEqual({mention.method for mention in result.mentions}, {"fuzzy"})
        self.assertTrue(all(0.75 <= mention.confidence < 1.0 for mention in result.mentions))

    def test_single_letter_f_requires_explicit_ticker_context(self):
        self.assertEqual(self.resolver.resolve("Give me a summary.").resolved_tickers, ())
        self.assertEqual(self.resolver.resolve("Compare ticker F with GM.").resolved_tickers, ("GM", "F"))
        self.assertEqual(self.resolver.resolve("Compare ticker f with GM.").resolved_tickers, ("GM", "F"))

    def test_language_collisions_and_out_of_corpus_mentions_clarify(self):
        for query in (
            "Does the aurora borealis affect sensors?",
            "Does alphabet soup appear?",
            "What is Toyota's strategy?",
        ):
            result = self.resolver.resolve(query)
            self.assertEqual(result.resolved_tickers, ())
            self.assertTrue(result.needs_clarification)
        self.assertTrue(self.resolver.resolve("What is toyota's strategy?").needs_clarification)

    def test_validated_llm_can_only_resolve_a_shortlisted_unresolved_mention(self):
        deterministic = self.resolver.resolve("Summarize Telsaaa revenue.")
        self.assertEqual(deterministic.resolved_tickers, ())
        self.assertEqual(deterministic.unresolved_mentions[0].candidate_tickers[0], "TSLA")

        resolved = self.resolver.apply_planner_resolution(
            deterministic,
            [{"raw_text": "Telsaaa", "ticker": "TSLA"}],
            ["TSLA"],
        )

        self.assertEqual(resolved.resolved_tickers, ("TSLA",))
        self.assertEqual(resolved.mentions[0].method, "llm")
        self.assertFalse(resolved.needs_clarification)

    def test_llm_cannot_invent_or_override_company(self):
        deterministic = self.resolver.resolve("Summarize Tesla revenue.")
        with self.assertRaisesRegex(ValueError, "disagree"):
            self.resolver.apply_planner_resolution(deterministic, [], [])

        unresolved = self.resolver.resolve("Summarize Telsaaa revenue.")
        with self.assertRaisesRegex(ValueError, "shortlist"):
            self.resolver.apply_planner_resolution(
                unresolved,
                [{"raw_text": "Telsaaa", "ticker": "GM"}],
                ["GM"],
            )

        out_of_corpus = self.resolver.resolve("Summarize Toyota revenue.")
        self.assertEqual(out_of_corpus.unresolved_mentions[0].candidate_tickers, ())
        with self.assertRaisesRegex(ValueError, "shortlist"):
            self.resolver.apply_planner_resolution(
                out_of_corpus,
                [{"raw_text": "Toyota", "ticker": "TSLA"}],
                ["TSLA"],
            )

    def test_redundant_matching_planner_mention_cannot_change_fuzzy_result(self):
        deterministic = self.resolver.resolve("What are frod's segments?")
        validated = self.resolver.apply_planner_resolution(
            deterministic,
            [{"raw_text": "Frod", "ticker": "F"}],
            ["F"],
        )
        self.assertEqual(validated.mentions, deterministic.mentions)
        with self.assertRaisesRegex(ValueError, "unrequested"):
            self.resolver.apply_planner_resolution(
                deterministic,
                [{"raw_text": "Frod", "ticker": "GM"}],
                ["GM"],
            )

    def test_internal_retrieval_query_adds_canonical_scope_only(self):
        original = "What are frod's risks?"
        internal = self.resolver.retrieval_query(original, ["F"])
        self.assertEqual(original, "What are frod's risks?")
        self.assertIn("Ford Motor Company (F)", internal)

    def test_damerau_distance_counts_adjacent_transposition_once(self):
        self.assertEqual(damerau_levenshtein("frod", "ford"), 1)


if __name__ == "__main__":
    unittest.main()
