from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
from uuid import uuid4

from src.conversations.repository import PostgresConversationRepository

from src.documents import (
    DocumentNotFoundError,
    DocumentService,
    DuplicateDocumentError,
    FilesystemAssetStore,
    InMemoryDocumentRepository,
    PostgresDocumentRepository,
)


class DocumentServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.repository = InMemoryDocumentRepository()
        self.allowed_conversations = {"chat-1"}
        self.service = DocumentService(
            self.repository,
            FilesystemAssetStore(Path(self.temporary.name) / "assets"),
            tenant_id="tenant-1",
            user_id="user-1",
            authorize_conversation=self._authorize,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _authorize(self, conversation_id):
        if conversation_id not in self.allowed_conversations:
            raise LookupError("Conversation was not found.")

    def test_upload_list_chunks_and_delete_are_conversation_scoped(self):
        document = self.service.upload(
            "chat-1", "notes.txt", "text/plain", b"Failover uses a passive replica."
        )
        self.assertEqual(self.service.list("chat-1"), [document])
        chunks = self.service.chunks("chat-1", document.id)
        self.assertEqual(chunks[0].text, "Failover uses a passive replica.")
        self.assertEqual(
            self.service.asset_store.read(document.asset_key),
            b"Failover uses a passive replica.",
        )
        self.service.delete("chat-1", document.id)
        self.assertEqual(self.service.list("chat-1"), [])
        with self.assertRaises(FileNotFoundError):
            self.service.asset_store.read(document.asset_key)

    def test_duplicate_in_same_chat_is_rejected_and_new_asset_is_rolled_back(self):
        content = b"Same immutable source."
        self.service.upload("chat-1", "one.txt", "text/plain", content)
        with self.assertRaises(DuplicateDocumentError):
            self.service.upload("chat-1", "two.txt", "text/plain", content)
        blobs = list((Path(self.temporary.name) / "assets").rglob("*.blob"))
        self.assertEqual(len(blobs), 1)

    def test_cross_chat_and_cross_owner_document_ids_are_not_visible(self):
        document = self.service.upload(
            "chat-1", "notes.txt", "text/plain", b"Private source."
        )
        other = DocumentService(
            self.repository,
            self.service.asset_store,
            tenant_id="tenant-2",
            user_id="user-2",
            authorize_conversation=lambda value: object(),
        )
        with self.assertRaises(DocumentNotFoundError):
            other.chunks("chat-1", document.id)
        with self.assertRaises(LookupError):
            self.service.list("chat-2")

    def test_index_failure_rolls_back_metadata_and_private_bytes(self):
        class FailingIndex:
            def upsert(self, document, chunks):
                raise RuntimeError("index unavailable")

        service = DocumentService(
            self.repository,
            self.service.asset_store,
            tenant_id="tenant-1",
            user_id="user-1",
            authorize_conversation=self._authorize,
            index=FailingIndex(),
        )
        with self.assertRaisesRegex(RuntimeError, "index unavailable"):
            service.upload(
                "chat-1", "rollback.txt", "text/plain", b"Rollback source."
            )
        self.assertEqual(service.list("chat-1"), [])
        self.assertEqual(
            list((Path(self.temporary.name) / "assets").rglob("*.blob")), []
        )


@unittest.skipUnless(
    os.getenv("AVA_TEST_POSTGRES_DSN"), "AVA_TEST_POSTGRES_DSN is not configured"
)
class PostgresDocumentServiceTests(unittest.TestCase):
    def test_live_owner_filtered_document_lifecycle(self):
        dsn = os.environ["AVA_TEST_POSTGRES_DSN"]
        conversations = PostgresConversationRepository(dsn)
        documents = PostgresDocumentRepository(dsn)
        tenant_id = f"tenant-{uuid4()}"
        user_id = f"user-{uuid4()}"
        conversation = conversations.create_conversation(
            tenant_id, user_id, "Upload test", False
        )
        with TemporaryDirectory() as directory:
            service = DocumentService(
                documents,
                FilesystemAssetStore(Path(directory) / "assets"),
                tenant_id=tenant_id,
                user_id=user_id,
                authorize_conversation=lambda value: conversations.get_conversation(
                    tenant_id, user_id, value
                ),
            )
            uploaded = service.upload(
                conversation.id,
                "evidence.txt",
                "text/plain",
                b"Owner-scoped evidence.",
            )
            self.assertEqual(service.list(conversation.id), [uploaded])
            with self.assertRaises(DocumentNotFoundError):
                documents.get(
                    "another-tenant",
                    user_id,
                    conversation.id,
                    uploaded.id,
                )
            service.delete(conversation.id, uploaded.id)
            self.assertEqual(service.list(conversation.id), [])
        conversations.delete_conversation(tenant_id, user_id, conversation.id)


if __name__ == "__main__":
    unittest.main()
