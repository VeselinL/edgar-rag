import json
import unittest

from src.evaluation.ava_p0 import (
    DEFAULT_LABEL_DIRECTORY,
    compare_baselines,
    load_cases,
    parity_fixture,
    percentile,
    validate_image_manifest,
)
from src.filings.corpus import COMPANY_ALIASES


class AvaP0DatasetTests(unittest.TestCase):
    def test_resolution_labels_cover_every_configured_alias(self):
        cases = load_cases(DEFAULT_LABEL_DIRECTORY, "company_resolution_v1.json")
        exact_queries = "\n".join(
            case["query"].casefold()
            for case in cases
            if case["category"].startswith("exact")
        )
        for aliases in COMPANY_ALIASES.values():
            for alias in aliases:
                self.assertIn(alias, exact_queries)

    def test_frozen_resolution_baseline_exposes_typo_gap(self):
        result = json.loads(
            (DEFAULT_LABEL_DIRECTORY / "baseline" / "baseline_summary.json").read_text(
                encoding="utf-8"
            )
        )["resolution"]
        self.assertEqual(result["summary"]["exact_accuracy"], 25 / 26)
        self.assertEqual(result["summary"]["typo_accuracy"], 0.0)
        self.assertTrue(
            any(
                record["id"] == "typo-frod"
                and record["first_failure_stage"] == "detection"
                for record in result["records"]
            )
        )

    def test_all_raw_image_nodes_have_frozen_labels(self):
        summary = validate_image_manifest(DEFAULT_LABEL_DIRECTORY)
        self.assertEqual(summary["node_count"], 35)
        self.assertEqual(summary["filing_count"], 10)
        self.assertTrue(summary["raw_html_match"])

    def test_history_labels_include_required_failure_classes(self):
        cases = load_cases(DEFAULT_LABEL_DIRECTORY, "conversation_history_v1.json")
        categories = {case["category"] for case in cases}
        self.assertTrue(
            {
                "follow_up",
                "topic_switch",
                "summary_recall",
                "deletion",
                "tenant_isolation",
                "conversation_isolation",
            }.issubset(categories)
        )


class AvaP0MetricTests(unittest.TestCase):
    def test_percentile_uses_linear_interpolation(self):
        self.assertEqual(percentile([1.0, 2.0, 3.0], 0.5), 2.0)
        self.assertAlmostEqual(percentile([1.0, 2.0], 0.95), 1.95)

    def test_frozen_citation_baseline_diagnoses_current_fallback(self):
        result = json.loads(
            (DEFAULT_LABEL_DIRECTORY / "baseline" / "baseline_summary.json").read_text(
                encoding="utf-8"
            )
        )["citations"]
        no_citation = next(
            record for record in result["records"] if record["id"] == "no-citations"
        )
        self.assertEqual(result["summary"]["source_display_exactness"], 3 / 7)
        self.assertEqual(no_citation["first_failure_stage"], "citation")
        self.assertTrue(no_citation["used_fallback"])

    def test_parity_fixture_excludes_latency_and_scores(self):
        baseline = {
            "policy": "test",
            "corpus": {"chunk_count": 1},
            "retrieval": {
                "records": [
                    {
                        "id": "case",
                        "detected_tickers": ["TSLA"],
                        "scope": "single_company",
                        "comparison": False,
                        "retrieval_scopes": ["single_company"],
                        "subqueries": ["query"],
                        "candidate_ids_by_company": {"TSLA": ["A"]},
                        "selected_ids": ["A"],
                        "selected_company_counts": {"TSLA": 1},
                        "coverage_by_subquery": [1],
                        "latency_ms": 123,
                    }
                ]
            },
        }
        fixture = parity_fixture(baseline)
        self.assertNotIn("latency_ms", fixture["cases"][0])

    def test_comparison_separates_quality_delta_from_id_changes(self):
        frozen = {
            "policy": "before",
            "corpus": {"fingerprint": "same"},
            "resolution": {"summary": {"accuracy": 0.5}},
            "retrieval": {
                "summary": {
                    "mean_candidate_gold_recall": 1.0,
                    "mean_final_gold_recall": 0.5,
                    "latency_ms": {"p50": 10.0},
                    "context_bge_token_proxy": {"mean": 100.0},
                },
                "records": [{
                    "id": "case",
                    "candidate_ids": ["A", "B"],
                    "selected_ids": ["A"],
                    "selected_company_counts": {"TSLA": 1},
                }],
            },
            "citations": {"summary": {"source_display_exactness": 0.5}},
        }
        current = json.loads(json.dumps(frozen))
        current["policy"] = "after"
        current["retrieval"]["summary"]["mean_final_gold_recall"] = 1.0
        current["retrieval"]["records"][0]["selected_ids"] = ["A", "B"]
        current["retrieval"]["records"][0]["selected_company_counts"] = {"TSLA": 2}

        comparison = compare_baselines(frozen, current)

        self.assertEqual(
            comparison["metrics"]["final_gold_recall"]["delta"], 0.5
        )
        self.assertEqual(
            comparison["retrieval_case_changes"][0]["change"],
            "ranking_or_selection_changed",
        )
        self.assertTrue(comparison["corpus_fingerprint_match"])

    def test_checked_in_baseline_matches_current_corpus_contract(self):
        path = DEFAULT_LABEL_DIRECTORY / "baseline" / "baseline_summary.json"
        baseline = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(baseline["corpus"]["chunk_count"], 4526)
        self.assertEqual(len(baseline["corpus"]["tickers"]), 11)
        self.assertEqual(baseline["retrieval"]["summary"]["case_count"], 5)
        self.assertEqual(baseline["citations"]["summary"]["case_count"], 7)


if __name__ == "__main__":
    unittest.main()
