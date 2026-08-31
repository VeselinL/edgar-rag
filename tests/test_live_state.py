"""Production-like PostgreSQL/Qdrant ownership, retention, and session gate."""

from datetime import datetime, timedelta, timezone
import os
import time
import unittest
from uuid import uuid4

import numpy as np

from src.auth.models import AuthSession
from src.auth.oidc import Principal
from src.auth.repository import PostgresAuthRepository
from src.conversations.maintenance import ConversationRetentionJob
from src.conversations.memory import QdrantMemoryStore
from src.conversations.models import MemoryItem
from src.conversations.repository import PostgresConversationRepository
from src.conversations.service import ConversationService
from src.indexing.qdrant_index import make_client


class FakeEmbedder:
    def encode(self, text, *, normalize_embeddings):
        vector = np.zeros(768, dtype=np.float32)
        vector[0] = 1.0
        return vector


@unittest.skipUnless(
    os.getenv("AVA_TEST_POSTGRES_DSN") and os.getenv("AVA_TEST_QDRANT_URL"),
    "Set live PostgreSQL and Qdrant test endpoints.",
)
class LiveStateIntegrationTests(unittest.TestCase):
    def test_owner_isolation_auth_session_and_retention_cascade(self):
        dsn = os.environ["AVA_TEST_POSTGRES_DSN"]
        suffix = uuid4().hex
        tenant_id = f"live-tenant-{suffix}"
        owner_id = f"owner-{suffix}"
        other_id = f"other-{suffix}"
        collection = f"ava_live_memory_{suffix}"
        repository = PostgresConversationRepository(dsn)
        auth_repository = PostgresAuthRepository(dsn)
        client = make_client(url=os.environ["AVA_TEST_QDRANT_URL"])
        for attempt in range(30):
            try:
                client.get_collections()
                break
            except Exception:
                if attempt == 29:
                    raise
                time.sleep(0.5)
        memory = QdrantMemoryStore(
            client,
            FakeEmbedder(),
            query_prefix="",
            collection_name=collection,
        )
        owner = ConversationService(
            repository,
            tenant_id=tenant_id,
            user_id=owner_id,
            memory_store=memory,
        )
        other = ConversationService(
            repository,
            tenant_id=tenant_id,
            user_id=other_id,
            memory_store=memory,
        )
        try:
            conversation = owner.create(memory_enabled=True)
            memory.upsert_summary(
                MemoryItem(
                    id=f"summary:{conversation.id}",
                    tenant_id=tenant_id,
                    user_id=owner_id,
                    conversation_id=conversation.id,
                    source_id="summary",
                    memory_type="summary",
                    content="Tesla preference",
                )
            )
            self.assertEqual(other.list(), [])
            self.assertEqual(
                memory.search(
                    "Tesla preference",
                    tenant_id,
                    other_id,
                    limit=5,
                    threshold=0,
                ),
                [],
            )

            now = datetime.now(timezone.utc)
            auth_repository.create_session(
                AuthSession(
                    session_hash=f"session-{suffix}",
                    csrf_hash=f"csrf-{suffix}",
                    principal=Principal(tenant_id, owner_id, f"subject-{suffix}"),
                    created_at=now - timedelta(hours=2),
                    expires_at=now - timedelta(hours=1),
                )
            )
            with repository._connect() as connection:
                connection.execute(
                    "UPDATE ava_conversations SET updated_at=%s WHERE conversation_id=%s",
                    (now - timedelta(days=91), conversation.id),
                )
            result = ConversationRetentionJob(
                repository, memory, auth_repository
            ).run(cutoff=now - timedelta(days=90), apply=True)
            self.assertEqual(result.deleted, 1)
            self.assertEqual(result.auth_sessions_deleted, 1)
            self.assertEqual(owner.list(), [])
            self.assertEqual(
                memory.search(
                    "Tesla preference",
                    tenant_id,
                    owner_id,
                    limit=5,
                    threshold=0,
                ),
                [],
            )
            with repository._connect() as connection:
                audit = connection.execute(
                    "SELECT scope FROM ava_deletion_audit WHERE tenant_id=%s",
                    (tenant_id,),
                ).fetchone()
            self.assertEqual(audit["scope"], "retention")
        finally:
            if client.collection_exists(collection):
                client.delete_collection(collection)
            with repository._connect() as connection:
                connection.execute(
                    "DELETE FROM ava_deletion_audit WHERE tenant_id=%s", (tenant_id,)
                )
                connection.execute(
                    "DELETE FROM ava_tenants WHERE tenant_id=%s", (tenant_id,)
                )
            client.close()


if __name__ == "__main__":
    unittest.main()
