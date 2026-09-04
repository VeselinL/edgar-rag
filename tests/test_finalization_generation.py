import unittest

from src.evaluation.finalization_generation import evaluate_oracle_context


class _Answer:
    text = "Tesla reported $1 [TSLA-1]."
    usage = {}


class _Service:
    def answer_with_metadata(self, query, evidence):
        return _Answer()


class FinalizationGenerationTests(unittest.TestCase):
    def test_oracle_context_rejects_no_valid_citation(self):
        result = evaluate_oracle_context([{
            "case_id": "case", "category": "direct_factual", "query": "Tesla",
            "gold_chunk_ids": ["TSLA-1"], "expects_abstention": False,
        }], {"TSLA-1": {"chunk_id": "TSLA-1", "text": "Revenue", "ticker": "TSLA"}}, _Service())

        self.assertTrue(result["records"][0]["citation_exact"])


if __name__ == "__main__":
    unittest.main()
