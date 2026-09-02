import json
from pathlib import Path
import unittest

from src.evaluation.calculator_quality import (
    evaluate_calculator,
    load_calculator_manifest,
)


class FakeCalculator:
    def calculate_query(self, query):
        if "broken" in query:
            raise ValueError("broken")
        return type(
            "Result",
            (),
            {"result": "4", "unit": None, "normalized_expression": "2 + 2"},
        )()


class CalculatorQualityTests(unittest.TestCase):
    def test_manifest_is_valid_and_unique(self):
        cases = load_calculator_manifest()
        self.assertEqual(len(cases), 10)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))

    def test_evaluator_records_exact_results_and_safe_errors(self):
        result = evaluate_calculator(
            [
                {"id": "pass", "query": "2 + 2", "result": "4", "unit": None},
                {"id": "fail", "query": "broken", "result": "0", "unit": None},
            ],
            FakeCalculator(),
        )
        self.assertEqual(result["summary"]["passed_count"], 1)
        self.assertEqual(result["summary"]["error_count"], 1)
        self.assertFalse(result["summary"]["gate_pass"])
        self.assertGreaterEqual(result["summary"]["latency_ms"]["mean"], 0.0)
        self.assertEqual(result["records"][1]["error"]["type"], "ValueError")

    def test_loader_rejects_duplicate_ids(self):
        path = Path(self.id().replace(".", "-") + ".json")
        try:
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cases": [
                            {"id": "same", "query": "2+2", "result": "4", "unit": None},
                            {"id": "same", "query": "3+3", "result": "6", "unit": None},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unique"):
                load_calculator_manifest(path)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
