import unittest

from src.evaluation.finalization_baseline import evaluate_retriever_only


class _Retriever:
    def retrieve(self, query, *, company_resolution):
        class Outcome:
            detected_companies = ("TSLA",)
            retrieval_scopes = ("TSLA",)
            candidates = ({"chunk": {"chunk_id": "TSLA-1"}},)
            chunk_ids = ("TSLA-1",)
        return Outcome()


class FinalizationBaselineTests(unittest.TestCase):
    def test_retriever_only_scores_candidate_and_final_gold(self):
        result = evaluate_retriever_only([{
            "case_id": "case", "category": "direct_factual", "query": "Tesla",
            "expected_tickers": ["TSLA"], "gold_chunk_ids": ["TSLA-1"],
            "expects_abstention": False,
        }], _Retriever())

        self.assertEqual(result["summary"]["candidate_recall"], 1.0)
        self.assertEqual(result["summary"]["gold_survival"], 1.0)
        self.assertEqual(result["summary"]["error_count"], 0)


if __name__ == "__main__":
    unittest.main()
