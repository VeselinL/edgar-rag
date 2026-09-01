import json
from pathlib import Path
import unittest

from src.orchestration.routing import (
    RequestRoute,
    RouteKind,
    RouteReason,
    deterministic_route,
    parse_route_decision,
    router_messages,
)
from src.resolution.companies import default_company_resolver


class RequestRoutingTests(unittest.TestCase):
    def resolution(self, query: str):
        return default_company_resolver.resolve(query)

    def test_greetings_bypass_model_and_filing_retrieval(self):
        for query in ("Hello", "Hi AVA!", "Good morning", "Thanks"):
            with self.subTest(query=query):
                route = deterministic_route(query, self.resolution(query))
                self.assertEqual(
                    route,
                    RequestRoute(
                        RouteKind.CONVERSATION_ONLY,
                        RouteReason.GREETING,
                        decided_by="deterministic",
                    ),
                )
                self.assertFalse(route.uses_filing_retrieval)

    def test_help_bypasses_retrieval_but_factual_query_does_not(self):
        route = deterministic_route("What can you do?", self.resolution("What can you do?"))
        self.assertEqual(route.reason_code, RouteReason.AVA_HELP)
        self.assertIsNone(
            deterministic_route(
                "What technology does Aurora use?",
                self.resolution("What technology does Aurora use?"),
            )
        )

    def test_explicit_filing_and_pure_arithmetic_routes_are_deterministic(self):
        filing = deterministic_route(
            "What does Tesla's 10-K say about autonomy?",
            self.resolution("What does Tesla's 10-K say about autonomy?"),
        )
        arithmetic = deterministic_route("What is 12.5 * 4?", self.resolution("12.5 * 4"))
        self.assertEqual(filing.route, RouteKind.FILING_RAG)
        self.assertEqual(arithmetic.route, RouteKind.CALCULATOR)
        self.assertTrue(arithmetic.arithmetic_required)

    def test_route_parser_rejects_inconsistent_calculation_and_missing_upload(self):
        with self.assertRaisesRegex(ValueError, "calculation route"):
            parse_route_decision(
                {
                    "route": "filing_rag",
                    "reason_code": "filing_evidence",
                    "arithmetic_required": True,
                }
            )
        with self.assertRaisesRegex(ValueError, "no uploads"):
            parse_route_decision(
                {
                    "route": "uploaded_document_rag",
                    "reason_code": "uploaded_evidence",
                    "arithmetic_required": False,
                }
            )

    def test_route_parser_accepts_bounded_combined_route(self):
        route = parse_route_decision(
            {
                "route": "filing_and_calculator",
                "reason_code": "evidence_arithmetic",
                "arithmetic_required": True,
            }
        )
        self.assertTrue(route.uses_filing_retrieval)
        self.assertTrue(route.uses_calculator)
        self.assertFalse(route.uses_web_search)

    def test_router_prompt_lists_product_aliases_and_separates_context(self):
        messages = router_messages(
            "How does its technology work?",
            self.resolution("How does its technology work?"),
            conversation_context="User: Tell me about Aurora Driver.",
        )
        self.assertIn("aurora driver", messages[2]["content"])
        self.assertIn("snapdragon digital chassis", messages[2]["content"])
        self.assertIn("Untrusted conversation context", messages[-2]["content"])
        self.assertEqual(messages[-1]["content"], "How does its technology work?")

    def test_frozen_route_manifest_has_unique_valid_cases(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "data/evaluation/ava_p0/v1/request_routing_v1.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload["cases"]
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        self.assertGreaterEqual(len(cases), 20)
        for case in cases:
            with self.subTest(case=case["id"]):
                route = parse_route_decision(
                    {
                        "route": case["route"],
                        "reason_code": case["reason_code"],
                        "arithmetic_required": case["arithmetic_required"],
                    },
                    uploads_available=bool(case["uploads"]),
                )
                self.assertEqual(route.route.value, case["route"])


if __name__ == "__main__":
    unittest.main()
