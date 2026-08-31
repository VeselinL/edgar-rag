import unittest

from src.evaluation.conversation_history import (
    evaluate_conversation_history,
    evaluate_planner_history,
    evaluate_state_cases,
    format_scope_evaluation_context,
    load_history_manifest,
)


class ScriptedPlanner:
    def plan_retrieval(
        self, query, deterministic_resolution=None, conversation_context=""
    ):
        contextual = {
            "How does it monetize that?": ["MBLY"],
            "And Ford?": ["F"],
            "Return to the first comparison: which uses EyeQ?": ["TSLA", "MBLY"],
        }
        query_only = {
            "How does it monetize that?": [],
            "And Ford?": ["F"],
            "Return to the first comparison: which uses EyeQ?": [],
        }
        tickers = (
            contextual.get(query, list(deterministic_resolution.resolved_tickers))
            if conversation_context
            else query_only.get(query, list(deterministic_resolution.resolved_tickers))
        )
        return {"resolved_tickers": tickers, "company_mentions": []}


class OneTurnFailurePlanner(ScriptedPlanner):
    def plan_retrieval(
        self, query, deterministic_resolution=None, conversation_context=""
    ):
        if query == "And Ford?" and not conversation_context:
            raise ValueError("synthetic planner contract failure")
        return super().plan_retrieval(
            query, deterministic_resolution, conversation_context
        )


class ConversationHistoryEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_history_manifest()

    def test_context_uses_runtime_shape_without_expected_ticker_labels(self):
        context = format_scope_evaluation_context(["Tell me about Mobileye."])
        self.assertIn("Recent conversation turns", context)
        self.assertIn("User: Tell me about Mobileye.", context)
        self.assertIn("Answer omitted", context)
        self.assertNotIn("MBLY", context)

    def test_planner_gate_measures_improvement_and_no_regression(self):
        result = evaluate_planner_history(self.cases, ScriptedPlanner())

        self.assertTrue(result["summary"]["gate_pass"])
        self.assertGreater(result["summary"]["history_accuracy_delta"], 0)
        self.assertEqual(result["summary"]["standalone_regression_count"], 0)
        self.assertEqual(result["summary"]["topic_switch_contextual_accuracy"], 1.0)

    def test_state_manifest_executes_deletion_and_isolation(self):
        result = evaluate_state_cases(self.cases)

        self.assertEqual(result["summary"]["case_count"], 4)
        self.assertTrue(result["summary"]["gate_pass"])

    def test_provider_failure_is_recorded_without_stopping_later_cases(self):
        result = evaluate_planner_history(self.cases, OneTurnFailurePlanner())

        self.assertEqual(result["summary"]["turn_count"], 9)
        self.assertEqual(result["summary"]["planner_error_count"], 1)
        failed = next(
            record for record in result["records"]
            if record["query_only_error"] is not None
        )
        self.assertEqual(failed["query_only_error"]["type"], "ValueError")
        self.assertEqual(result["records"][-1]["case_id"], "old-turn-recall")

    def test_combined_gate_requires_planner_and_state_success(self):
        result = evaluate_conversation_history(self.cases, ScriptedPlanner())

        self.assertTrue(result["gate_pass"])
        self.assertTrue(result["planner"]["summary"]["gate_pass"])
        self.assertTrue(result["state"]["summary"]["gate_pass"])


if __name__ == "__main__":
    unittest.main()
