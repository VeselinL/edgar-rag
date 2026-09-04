import unittest

from src.evaluation.finalization_planner import evaluate_planner_retriever


class _Planner:
    def plan_retrieval(self, query, deterministic, context, *, selected_tickers):
        return {"company_mentions": [], "resolved_tickers": ["TSLA"], "subqueries": [{"query": query, "tickers": ["TSLA"]}]}


class _Retriever:
    def retrieve(self, *args):
        class Outcome:
            candidates = ({"chunk": {"chunk_id": "TSLA-1"}},)
            chunk_ids = ("TSLA-1",)
        return Outcome()


class FinalizationPlannerTests(unittest.TestCase):
    def test_planner_retriever_scores_planned_gold_evidence(self):
        result = evaluate_planner_retriever([{
            "case_id": "case", "category": "direct_factual", "query": "Tesla",
            "history": [], "selected_company_scope": ["TSLA"], "expected_tickers": ["TSLA"],
            "gold_chunk_ids": ["TSLA-1"],
        }], _Planner(), _Retriever())

        self.assertEqual(result["records"][0]["candidate_recall"], 1.0)
        self.assertTrue(result["records"][0]["scope_exact"])


if __name__ == "__main__":
    unittest.main()
