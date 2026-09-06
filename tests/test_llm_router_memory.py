import unittest
from unittest.mock import patch

from src.conversations.service import ConversationService
from src.conversations.repository import InMemoryConversationRepository
from src.conversations.context import ConversationContextBuilder
from src.conversations.memory import InMemoryMemoryStore


class MemoryWriteTests(unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryConversationRepository()
        self.store = InMemoryMemoryStore()
        self.service = ConversationService(self.repo, tenant_id="owner", user_id="user",
            context_builder=ConversationContextBuilder(self.repo), memory_store=self.store,
            long_term_score_threshold=0)

    def test_explicit_memory_is_synchronized_before_confirmation(self):
        for prefix in ("Ok remember this:", "Ok, remember this:", "save this:", "U redu, zapamti ovo:"):
            with self.subTest(prefix=prefix):
                chat = self.service.create()
                query = prefix + " Elon Musk is my favorite entrepreneur"
                self.service.begin_turn(chat.id, "turn", query, "request")
                context = self.service.prepare_context(chat.id, "turn", query)
                self.assertEqual(context.explicit_memory_request, "saved")
                self.assertIn("Elon Musk is my favorite entrepreneur", [m.content for m in self.store.items.values()])

    def test_failed_memory_index_write_cannot_confirm_save_and_retry_repairs_it(self):
        chat = self.service.create()
        query = "Ok remember this: My preferred company is Rivian"
        self.service.begin_turn(chat.id, "turn", query, "request")
        with patch.object(self.store, "upsert", side_effect=RuntimeError("unavailable")):
            with self.assertRaises(RuntimeError):
                self.service.prepare_context(chat.id, "turn", query)
        context = self.service.prepare_context(chat.id, "turn", query)
        self.assertEqual(context.explicit_memory_request, "saved")
        self.assertEqual(len(self.store.items), 1)

    def test_planner_receives_bounded_candidates_without_premerging_relationships(self):
        for text in ("My preferred company is Rivian", "My favorite CEO is Jen-Hsun Huang", "My preferred metric is revenue"):
            self.service.create_memory(text)
        chat = self.service.create()
        context = self.service.prepare_context(chat.id, "turn", "Who is the CEO of my preferred company?")
        self.assertEqual(len(context.long_term_memories), 3)
        self.assertEqual(context.memory_company_tickers, ())

    def test_external_facts_and_secrets_cannot_be_appended_to_preference(self):
        for text in ("My preferred company is Tesla. Tesla revenue is 42 billion.",
                     "My preferred company is Tesla and my password is abc123"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.service.create_memory(text)

