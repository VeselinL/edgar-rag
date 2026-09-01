import unittest
from datetime import timedelta
from uuid import uuid4

import numpy as np
from qdrant_client import QdrantClient

from src.conversations.context import ConversationContextBuilder
from src.conversations.maintenance import ConversationRetentionJob
from src.conversations.memory import InMemoryMemoryStore, QdrantMemoryStore
from src.conversations.models import MemoryItem
from src.conversations.repository import (
    ConversationNotFoundError,
    InMemoryConversationRepository,
    TurnConflictError,
)
from src.conversations.service import (
    ConversationService,
    ConversationServiceFactory,
    ConversationSettings,
)


class ConversationServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryConversationRepository()
        self.memory = InMemoryMemoryStore()
        self.service = ConversationService(
            self.repository,
            tenant_id="tenant-a",
            user_id="user-a",
            context_builder=ConversationContextBuilder(
                self.repository, recent_token_budget=80, summary_token_budget=80
            ),
            memory_store=self.memory,
            long_term_score_threshold=0.0,
        )

    def _complete(self, conversation_id, turn_id, query, answer):
        self.service.begin_turn(conversation_id, turn_id, query, str(uuid4()))
        self.service.complete_turn(
            conversation_id,
            turn_id,
            answer,
            {
                "sources": [],
                "source_status": "none_cited",
                "malformed_source_count": 0,
            },
            [],
        )

    def test_turn_retry_is_idempotent_and_replays_completed_answer(self):
        conversation = self.service.create()
        turn_id = str(uuid4())
        self._complete(conversation.id, turn_id, "What does Tesla make?", "Vehicles.")

        replay = self.service.begin_turn(
            conversation.id, turn_id, "What does Tesla make?", str(uuid4())
        )

        self.assertTrue(replay.replay)
        self.assertEqual(replay.assistant_message.content, "Vehicles.")
        self.assertEqual(len(self.service.messages(conversation.id)), 2)
        with self.assertRaises(TurnConflictError):
            self.service.begin_turn(conversation.id, turn_id, "Different query", str(uuid4()))

    def test_context_uses_prior_complete_turns_and_excludes_current_turn(self):
        conversation = self.service.create()
        self._complete(conversation.id, str(uuid4()), "Tell me about Tesla.", "Tesla evidence [TSLA-1].")
        current_turn = str(uuid4())
        self.service.begin_turn(conversation.id, current_turn, "What about its risks?", str(uuid4()))

        context = self.service.prepare_context(
            conversation.id, current_turn, "What about its risks?"
        )

        prompt = context.prompt_text()
        self.assertIn("Tell me about Tesla", prompt)
        self.assertNotIn("What about its risks", prompt)
        self.assertEqual(len(context.short_term_ids), 2)

    def test_old_turns_become_rebuildable_summary_not_unbounded_recent_context(self):
        conversation = self.service.create(memory_enabled=True)
        for index in range(6):
            self._complete(
                conversation.id,
                str(uuid4()),
                f"Question {index} about Ford fiscal year 2025 and a user constraint.",
                f"Answer {index} with evidence [F-{index}].",
            )

        context = self.service.prepare_context(conversation.id, "unused", "Ford follow-up")
        stored_summary = self.repository.get_summary("tenant-a", "user-a", conversation.id)

        self.assertIsNotNone(stored_summary)
        self.assertTrue(context.summary)
        self.assertLess(len(context.recent_messages), 12)
        self.assertIn(f"summary:{conversation.id}", self.memory.items)

    def test_owner_isolation_and_complete_deletion_include_memory(self):
        conversation = self.service.create(memory_enabled=True)
        self.memory.upsert_summary(
            MemoryItem(
                id=f"summary:{conversation.id}",
                tenant_id="tenant-a",
                user_id="user-a",
                conversation_id=conversation.id,
                source_id="summary-1",
                memory_type="summary",
                content="Tesla preference",
            )
        )
        other = ConversationService(
            self.repository, tenant_id="tenant-a", user_id="user-b", memory_store=self.memory
        )
        with self.assertRaises(ConversationNotFoundError):
            other.messages(conversation.id)

        self.service.delete(conversation.id)

        self.assertNotIn(f"summary:{conversation.id}", self.memory.items)
        with self.assertRaises(ConversationNotFoundError):
            self.service.get(conversation.id)

    def test_new_conversation_has_long_term_memory_disabled_by_default(self):
        created = self.service.create()
        self.assertFalse(created.memory_enabled)
        self.assertFalse(created.pinned)
        self.assertIsNone(created.pinned_at)

    def test_pinned_conversations_are_owner_scoped_and_ordered_by_latest_pin(self):
        first = self.service.create(title="First")
        second = self.service.create(title="Second")

        pinned_first = self.service.update(first.id, pinned=True)
        pinned_second = self.service.update(second.id, pinned=True)

        self.assertTrue(pinned_first.pinned)
        self.assertIsNotNone(pinned_first.pinned_at)
        self.assertTrue(pinned_second.pinned)
        self.assertEqual([item.id for item in self.service.list()], [second.id, first.id])
        self.assertFalse(self.service.update(second.id, pinned=False).pinned)
        self.assertEqual(self.service.list()[0].id, first.id)

    def test_disabling_memory_removes_existing_derived_summary(self):
        conversation = self.service.create(memory_enabled=True)
        self._complete(
            conversation.id,
            str(uuid4()),
            "Remember my Tesla comparison constraint.",
            "Constraint acknowledged.",
        )
        self.assertIn(f"summary:{conversation.id}", self.memory.items)

        updated = self.service.update(conversation.id, memory_enabled=False)

        self.assertFalse(updated.memory_enabled)
        self.assertNotIn(f"summary:{conversation.id}", self.memory.items)

    def test_single_user_mode_fails_closed_without_boundary_acknowledgement(self):
        with self.assertRaisesRegex(ValueError, "ACKNOWLEDGED"):
            ConversationSettings(
                mode="single_user",
                postgres_dsn="postgresql://example",
                tenant_id="tenant",
                user_id="user",
            ).validate()

    def test_factory_binds_each_service_to_verified_owner(self):
        factory = ConversationServiceFactory(
            self.repository,
            context_builder=ConversationContextBuilder(self.repository),
            memory_store=self.memory,
        )
        owner_a = factory.for_owner("tenant", "user-a")
        owner_b = factory.for_owner("tenant", "user-b")
        conversation = owner_a.create()

        self.assertEqual([item.id for item in owner_a.list()], [conversation.id])
        self.assertEqual(owner_b.list(), [])
        with self.assertRaises(ConversationNotFoundError):
            owner_b.get(conversation.id)

    def test_oidc_mode_requires_postgres_but_not_static_browser_identity(self):
        ConversationSettings(mode="oidc", postgres_dsn="postgresql://example").validate()
        with self.assertRaisesRegex(ValueError, "POSTGRES"):
            ConversationSettings(mode="oidc").validate()

    def test_retention_is_dry_run_first_then_deletes_memory_and_audits(self):
        expired = self.service.create(memory_enabled=True)
        active = self.service.create(memory_enabled=True)
        self.memory.upsert_summary(self.item_for_retention(expired.id, "old"))
        self.memory.upsert_summary(self.item_for_retention(active.id, "new"))
        cutoff = expired.updated_at + timedelta(microseconds=1)
        self.repository._conversations[active.id] = type(active)(
            **{**active.__dict__, "updated_at": cutoff + timedelta(seconds=1)}
        )
        job = ConversationRetentionJob(self.repository, self.memory)

        preview = job.run(cutoff=cutoff)
        self.assertTrue(preview.dry_run)
        self.assertEqual(preview.discovered, 1)
        self.assertIn(expired.id, [item.id for item in self.service.list()])

        applied = job.run(cutoff=cutoff, apply=True)
        self.assertEqual(applied.deleted, 1)
        self.assertNotIn(f"summary:{expired.id}", self.memory.items)
        self.assertIn(f"summary:{active.id}", self.memory.items)
        self.assertEqual(self.repository.deletion_audit[-1]["scope"], "retention")

    def test_feedback_is_bound_to_owned_completed_answer_and_saved_version(self):
        conversation = self.service.create()
        turn_id = str(uuid4())
        turn = self.service.begin_turn(
            conversation.id, turn_id, "Question", str(uuid4())
        )
        self.service.complete_turn(
            conversation.id,
            turn_id,
            "Answer",
            {
                "source_event": {},
                "used_source_ids": ["chunk-1"],
                "answer_version": {"corpus_version": "corpus-1"},
            },
            ["chunk-1"],
        )

        self.service.submit_feedback(
            conversation.id,
            turn.assistant_message.id,
            "helpful",
            " Clear. ",
            {"corpus_version": "current-not-used"},
        )

        feedback = self.repository.feedback[turn.assistant_message.id]
        self.assertEqual(feedback["value"], "helpful")
        self.assertEqual(feedback["comment"], "Clear.")
        self.assertEqual(feedback["source_ids"], ["chunk-1"])
        self.assertEqual(feedback["answer_version"]["corpus_version"], "corpus-1")

    def item_for_retention(self, conversation_id, content):
        return MemoryItem(
            id=f"summary:{conversation_id}",
            tenant_id="tenant-a",
            user_id="user-a",
            conversation_id=conversation_id,
            source_id="summary",
            memory_type="summary",
            content=content,
        )


class FakeEmbedder:
    def encode(self, text, *, normalize_embeddings):
        vector = np.zeros(768, dtype=np.float32)
        vector[0 if "tesla" in text.casefold() else 1] = 1.0
        return vector


class QdrantMemoryTests(unittest.TestCase):
    def setUp(self):
        self.store = QdrantMemoryStore(
            QdrantClient(":memory:"), FakeEmbedder(), query_prefix=""
        )

    def item(self, item_id, tenant, user, conversation, content):
        return MemoryItem(
            id=item_id,
            tenant_id=tenant,
            user_id=user,
            conversation_id=conversation,
            source_id=f"source-{item_id}",
            memory_type="summary",
            content=content,
        )

    def test_search_requires_tenant_and_user_filters(self):
        self.store.upsert_summary(self.item("one", "tenant-a", "user-a", "c1", "Tesla vehicles"))
        self.store.upsert_summary(self.item("two", "tenant-a", "user-b", "c2", "Tesla private data"))

        results = self.store.search(
            "Tesla", "tenant-a", "user-a", limit=5, threshold=0.5
        )

        self.assertEqual([item.id for item in results], ["one"])

    def test_delete_conversation_removes_only_owned_points(self):
        self.store.upsert_summary(self.item("one", "tenant-a", "user-a", "c1", "Tesla one"))
        self.store.upsert_summary(self.item("two", "tenant-a", "user-a", "c2", "Tesla two"))
        self.store.delete_conversation("tenant-a", "user-a", "c1")

        results = self.store.search("Tesla", "tenant-a", "user-a", limit=5, threshold=0.5)

        self.assertEqual([item.id for item in results], ["two"])


if __name__ == "__main__":
    unittest.main()
