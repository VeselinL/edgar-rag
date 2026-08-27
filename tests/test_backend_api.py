import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.backend.app import create_app, safe_stream_error
from src.backend.pipeline import MockPipeline


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for frame in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in frame.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


class BackendApiTests(unittest.TestCase):
    def setUp(self):
        environment = {
            "AVA_PIPELINE_MODE": "mock",
            "AVA_QUERY_MAX_LENGTH": "40",
        }
        self.environment = patch.dict(os.environ, environment, clear=False)
        self.environment.start()
        self.client_context = TestClient(create_app(pipeline=MockPipeline(delay_seconds=0)))
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.environment.stop()

    def test_health_distinguishes_mode_and_readiness(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "mode": "mock",
                "pipeline_ready": True,
                "answer_delivery": "mock_streaming",
            },
        )

    def test_successful_sse_order(self):
        response = self.client.post("/api/chat/stream", json={"query": "What does Tesla do?"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertRegex(response.headers["x-request-id"], r"^[0-9a-f-]{36}$")
        events = parse_sse(response.text)
        self.assertEqual([event for event, _ in events], ["delta", "delta", "delta", "sources", "done"])
        self.assertEqual(len(events[-2][1]["sources"]), 2)
        self.assertEqual(events[-2][1]["source_status"], "cited")
        self.assertNotIn("citation_fallback", events[-2][1])

    def test_pre_token_failure_is_safe(self):
        response = self.client.post("/api/chat/stream", json={"query": "[mock:pre-error]"})
        events = parse_sse(response.text)
        self.assertEqual(
            events,
            [
                (
                    "error",
                    {
                        "message": (
                            "The filing-analysis service is temporarily unavailable. "
                            "Please retry shortly."
                        )
                    },
                )
            ],
        )
        self.assertNotIn("Deterministic", response.text)
        self.assertNotIn("could not complete this response", response.text)

    def test_plan_validation_failure_does_not_blame_user_wording(self):
        message = safe_stream_error(ValueError("raw internal planner details"))

        self.assertIn("temporary service issue", message)
        self.assertNotIn("restate", message.casefold())
        self.assertNotIn("wording", message.casefold())
        self.assertNotIn("raw internal", message)
        self.assertNotIn("could not complete this response", message)

    def test_mid_stream_failure_preserves_delta_then_errors(self):
        response = self.client.post("/api/chat/stream", json={"query": "[mock:mid-error]"})
        events = parse_sse(response.text)
        self.assertEqual([event for event, _ in events], ["delta", "error"])

    def test_empty_and_over_limit_queries_are_rejected_before_stream(self):
        empty = self.client.post("/api/chat/stream", json={"query": "   "})
        long = self.client.post("/api/chat/stream", json={"query": "x" * 41})
        self.assertEqual(empty.status_code, 422)
        self.assertEqual(long.status_code, 422)
        self.assertEqual(empty.headers["content-type"].split(";")[0], "application/json")


if __name__ == "__main__":
    unittest.main()
