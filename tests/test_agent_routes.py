import unittest

from src.evaluation.agent_routes import (
    evaluate_agent_routes,
    load_agent_routes,
    parse_arguments,
)
from src.orchestration.routing import deterministic_route
from src.orchestration.routing import RequestRoute, RouteKind, RouteReason
from src.resolution.companies import default_company_resolver


class DeterministicRouter:
    def route_request(self, query, resolution, context, uploads):
        route = deterministic_route(query, resolution, uploads_available=bool(uploads), conversation_context=context)
        if route is None:
            raise ValueError("case requires a model route")
        return route


class AgentRouteTests(unittest.TestCase):
    def test_manifest_has_reviewed_required_distribution(self):
        self.assertEqual(len(load_agent_routes()), 60)

    def test_evaluator_accepts_a_freeze_manifest(self):
        arguments = parse_arguments(["--freeze-manifest", "frozen.json"])
        self.assertEqual(str(arguments.freeze_manifest), "frozen.json")

    def test_deterministic_routes_cover_every_manifest_case(self):
        result = evaluate_agent_routes(load_agent_routes(), DeterministicRouter())
        self.assertEqual(result["summary"]["case_count"], 60)

    def test_conversation_route_does_not_report_resolution_as_evidence_scope(self):
        class ConversationRouter:
            def route_request(self, *args):
                return RequestRoute(RouteKind.CONVERSATION, RouteReason.OUT_OF_SCOPE)

        result = evaluate_agent_routes(
            [{"case_id": "scope", "category": "conversation_memory", "query": "Should I buy TSLA?", "expected_route": "conversation", "expected_tickers": [], "expected_tool_sequence": []}],
            ConversationRouter(),
        )
        self.assertEqual(result["records"][0]["actual_tickers"], [])


if __name__ == "__main__":
    unittest.main()
