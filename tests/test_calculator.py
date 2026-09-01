import unittest

from src.tools.calculator import CalculationError, CalculatorTool


class CalculatorToolTests(unittest.TestCase):
    def setUp(self):
        self.calculator = CalculatorTool()

    def test_operator_precedence_and_decimal_result(self):
        record = self.calculator.calculate_query("Calculate 12.5 * 4 + 2")
        self.assertEqual(record.result, "52")
        self.assertEqual(record.operands, ("12.5", "4", "2"))
        self.assertEqual(record.operators, ("*", "+"))

    def test_percentage_ratio_sum_and_rounding(self):
        percentage = self.calculator.calculate_query(
            "Calculate 18 as a percentage of 72."
        )
        ratio = self.calculator.calculate_query("Calculate the ratio of 18 to 6")
        total = self.calculator.calculate_query("Calculate the total of 1, 2 and 3")
        rounded = self.calculator.calculate_query(
            "Calculate 2 / 3, rounded to 2 decimal places"
        )
        self.assertEqual((percentage.result, percentage.unit), ("25", "%"))
        self.assertEqual(ratio.result, "3")
        self.assertEqual(total.result, "6")
        self.assertEqual(rounded.result, "0.67")
        self.assertEqual(rounded.rounding_rule, "round-half-even to 2 decimal places")

    def test_evidence_operations_are_constructed_by_allow_list(self):
        difference = self.calculator.calculate_operation(
            "difference", ["100.5", "40.25"], unit="USD millions"
        )
        growth = self.calculator.calculate_operation("growth_rate", ["80", "100"])
        self.assertEqual(difference.result, "60.25")
        self.assertEqual(difference.unit, "USD millions")
        self.assertEqual((growth.result, growth.unit), ("25", "%"))

    def test_rejects_code_names_exponents_and_division_by_zero(self):
        for query in (
            "__import__('os')",
            "2 ** 8",
            "1e6 + 2",
            "10 / 0",
            "open('/tmp/value')",
        ):
            with self.subTest(query=query), self.assertRaises(CalculationError):
                self.calculator.calculate_query(query)

    def test_rejects_unsupported_evidence_operation(self):
        with self.assertRaisesRegex(CalculationError, "unsupported"):
            self.calculator.calculate_operation("median", ["1", "2"])


if __name__ == "__main__":
    unittest.main()
