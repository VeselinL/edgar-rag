import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from src.backend.app import create_app, safe_stream_error
from src.backend.pipeline import MockPipeline
from src.backend.pipeline import PipelineEvent
from src.conversations.repository import InMemoryConversationRepository
from src.conversations.service import ConversationService


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
                "conversation_history": {
                    "enabled": False,
                    "deployment_boundary": "stateless",
                    "long_term_store": "disabled",
                },
            },
        )

    def test_health_exposes_unavailable_qdrant_without_mock_fallback(self):
        class UnavailableRealPipeline:
            mode = "real"
            ready = False
            answer_delivery = "buffered"
            qdrant_health = {
                "configured": True,
                "mode": "primary",
                "status": "unavailable",
                "safe_error_class": "provider_transport_error",
            }

        with TestClient(create_app(pipeline=UnavailableRealPipeline())) as client:
            health = client.get("/api/health")
            chat = client.post(
                "/api/chat/stream", json={"query": "What are Tesla's risks?"}
            )
        self.assertFalse(health.json()["pipeline_ready"])
        self.assertEqual(health.json()["mode"], "real")
        self.assertEqual(health.json()["qdrant"]["status"], "unavailable")
        self.assertEqual(chat.status_code, 503)

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

    def test_persistent_turn_is_idempotent_and_can_be_resumed(self):
        repository = InMemoryConversationRepository()
        service = ConversationService(repository, tenant_id="tenant", user_id="user")
        with TestClient(
            create_app(
                pipeline=MockPipeline(delay_seconds=0),
                conversation_service=service,
            )
        ) as client:
            created = client.post(
                "/api/conversations",
                json={"title": "New conversation", "memory_enabled": False},
            ).json()
            turn_id = str(uuid4())
            payload = {
                "query": "What does Tesla do?",
                "conversation_id": created["id"],
                "client_turn_id": turn_id,
            }
            first = client.post("/api/chat/stream", json=payload)
            replay = client.post("/api/chat/stream", json=payload)
            messages = client.get(
                f"/api/conversations/{created['id']}/messages"
            ).json()["messages"]

        self.assertEqual(first.status_code, 200)
        self.assertEqual([event for event, _ in parse_sse(replay.text)], ["delta", "sources", "done"])
        self.assertTrue(parse_sse(replay.text)[-1][1]["replayed"])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[1]["role"], "assistant")

    def test_history_routes_are_owner_scoped_and_deletion_is_complete(self):
        repository = InMemoryConversationRepository()
        owner = ConversationService(repository, tenant_id="tenant", user_id="owner")
        other = ConversationService(repository, tenant_id="tenant", user_id="other")
        conversation = owner.create()

        with TestClient(
            create_app(pipeline=MockPipeline(delay_seconds=0), conversation_service=other)
        ) as client:
            hidden = client.get(f"/api/conversations/{conversation.id}/messages")

        self.assertEqual(hidden.status_code, 404)

    def test_interrupted_persistent_turn_is_retryable_without_duplicate_user_message(self):
        class InterruptedPipeline:
            mode = "real"
            ready = True
            answer_delivery = "provider_streaming"

            async def stream(self, *args, **kwargs):
                yield PipelineEvent("delta", {"text": "Partial"})

        repository = InMemoryConversationRepository()
        service = ConversationService(repository, tenant_id="tenant", user_id="user")
        conversation = service.create()
        turn_id = str(uuid4())
        payload = {
            "query": "What does Tesla do?",
            "conversation_id": conversation.id,
            "client_turn_id": turn_id,
        }
        with TestClient(
            create_app(pipeline=InterruptedPipeline(), conversation_service=service)
        ) as client:
            first = client.post("/api/chat/stream", json=payload)
            second = client.post("/api/chat/stream", json=payload)
            messages = client.get(
                f"/api/conversations/{conversation.id}/messages"
            ).json()["messages"]

        self.assertEqual([event for event, _ in parse_sse(first.text)], ["delta"])
        self.assertEqual([event for event, _ in parse_sse(second.text)], ["delta"])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1]["status"], "failed")

    def test_persistent_chat_rejects_non_uuid_turn_identifiers(self):
        repository = InMemoryConversationRepository()
        service = ConversationService(repository, tenant_id="tenant", user_id="user")
        with TestClient(
            create_app(pipeline=MockPipeline(delay_seconds=0), conversation_service=service)
        ) as client:
            response = client.post(
                "/api/chat/stream",
                json={
                    "query": "Question",
                    "conversation_id": "browser-owned-name",
                    "client_turn_id": "turn-1",
                },
            )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
