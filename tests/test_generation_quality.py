import unittest

from src.evaluation.generation_quality import (
    evaluate_generation_quality,
    load_chunk_lookup,
    load_generation_cases,
    score_generation_answer,
)


class GenerationQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_generation_cases()
        cls.chunks = load_chunk_lookup()

    def test_cases_cover_required_generation_categories(self):
        categories = {case["category"] for case in self.cases}
        self.assertTrue(
            {"direct_factual", "table", "numerical", "calculation",
             "cross_section_synthesis", "cross_company_comparison",
             "absent_abstention"}.issubset(categories)
        )

    def test_reference_suite_passes_every_separate_metric(self):
        result = evaluate_generation_quality(self.cases, self.chunks)
        summary = result["summary"]
        for metric in (
            "completeness", "labeled_claim_support", "numerical_correctness",
            "abstention_accuracy", "comparison_coverage", "citation_precision",
            "citation_recall", "source_display_exactness",
        ):
            self.assertEqual(summary[metric], 1.0, metric)
        self.assertEqual(summary["contradiction_rate"], 0.0)
        self.assertEqual(summary["invalid_citation_count"], 0)
        self.assertEqual(summary["uncited_labeled_fact_count"], 0)

    def test_wrong_number_and_missing_citation_fail_distinct_metrics(self):
        case = next(item for item in self.cases if item["id"] == "numerical-tsla-revenue")
        evidence = [{"chunk": self.chunks[item]} for item in case["final_evidence_ids"]]
        score = score_generation_answer(case, "Tesla's 2025 revenue was 97,690 million.", evidence)
        self.assertEqual(score["correct_numerical_fact_count"], 0)
        self.assertEqual(score["citation_recall"], 0.0)


if __name__ == "__main__":
    unittest.main()
