import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.evaluation.request_routing import (
    evaluate_request_routes,
    load_routing_manifest,
)
from src.orchestration.routing import RequestRoute, RouteKind, RouteReason


class FixedRouter:
    def __init__(self, routes):
        self.routes = iter(routes)

    def route_request(self, query, resolution, context, uploads):
        return next(self.routes)


class RequestRoutingEvaluationTests(unittest.TestCase):
    def test_reason_code_difference_is_diagnostic_when_behavior_matches(self):
        case = {
            "id": "web-calculation",
            "query": "Using today's prices, calculate the difference.",
            "context": "",
            "uploads": [],
            "route": "web_and_calculator",
            "reason_code": "evidence_arithmetic",
            "arithmetic_required": True,
        }
        router = FixedRouter(
            [
                RequestRoute(
                    RouteKind.WEB_AND_CALCULATOR,
                    RouteReason.CURRENT_OR_EXTERNAL,
                    True,
                )
            ]
        )
        result = evaluate_request_routes([case], router)
        self.assertTrue(result["summary"]["gate_pass"])
        self.assertEqual(result["summary"]["reason_code_accuracy"], 0.0)

    def test_manifest_loader_rejects_duplicate_ids(self):
        case = {
            "id": "duplicate",
            "query": "Hello",
            "context": "",
            "uploads": [],
            "route": "conversation_only",
            "reason_code": "greeting",
            "arithmetic_required": False,
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(
                json.dumps({"schema_version": 1, "cases": [case, case]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unique"):
                load_routing_manifest(path)


if __name__ == "__main__":
    unittest.main()
