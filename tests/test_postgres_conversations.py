"""Opt-in live PostgreSQL contract test for CI and deployment verification."""

import os
import unittest
from uuid import uuid4

from src.conversations.repository import PostgresConversationRepository
from src.conversations.service import ConversationService


@unittest.skipUnless(
    os.getenv("AVA_TEST_POSTGRES_DSN"),
    "Set AVA_TEST_POSTGRES_DSN to run the live PostgreSQL conversation contract.",
)
class PostgresConversationIntegrationTests(unittest.TestCase):
    def test_migration_idempotency_turn_replay_and_cascade_delete(self):
        repository = PostgresConversationRepository(os.environ["AVA_TEST_POSTGRES_DSN"])
        repository.migrate()
        tenant_id = f"test-tenant-{uuid4()}"
        user_id = f"test-user-{uuid4()}"
        service = ConversationService(repository, tenant_id=tenant_id, user_id=user_id)
        conversation = service.create(memory_enabled=False)
        pinned = service.update(conversation.id, pinned=True)
        self.assertTrue(pinned.pinned)
        self.assertIsNotNone(pinned.pinned_at)
        turn_id = str(uuid4())
        request_id = str(uuid4())

        service.begin_turn(conversation.id, turn_id, "What does Tesla do?", request_id)
        service.complete_turn(
            conversation.id,
            turn_id,
            "Tesla answer [TSLA-2025-CHUNK-000001]",
            {
                "sources": [],
                "source_status": "none_cited",
                "malformed_source_count": 0,
            },
            ["TSLA-2025-CHUNK-000001"],
        )
        replay = service.begin_turn(
            conversation.id, turn_id, "What does Tesla do?", str(uuid4())
        )

        self.assertTrue(replay.replay)
        self.assertEqual(len(service.messages(conversation.id)), 2)
        service.delete(conversation.id)
        self.assertEqual(service.list(), [])


if __name__ == "__main__":
    unittest.main()
