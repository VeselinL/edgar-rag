from datetime import datetime, timezone
import unittest

import numpy as np
from qdrant_client import QdrantClient

from src.documents.models import StoredDocumentChunk, UploadedDocument
from src.documents.retrieval import QdrantDocumentIndex


class FakeEmbedder:
    def encode(self, values, *, normalize_embeddings=True):
        if isinstance(values, str):
            values = [values]
        vectors = []
        for value in values:
            vector = np.zeros(768, dtype=np.float32)
            vector[0] = 1.0 if "failover" in value.casefold() else 0.0
            vector[1] = 0.0 if vector[0] else 1.0
            vectors.append(vector)
        return np.asarray(vectors)


def document(identifier, tenant, user, conversation, filename="source.txt"):
    now = datetime.now(timezone.utc)
    return UploadedDocument(
        identifier,
        conversation,
        tenant,
        user,
        filename,
        "text/plain",
        10,
        "a" * 64,
        identifier,
        "ready",
        None,
        4,
        1,
        now,
        now,
    )


class QdrantDocumentIndexTests(unittest.TestCase):
    def test_search_and_delete_require_exact_owner_and_chat_filters(self):
        client = QdrantClient(":memory:")
        index = QdrantDocumentIndex(client, FakeEmbedder(), query_prefix="query: ")
        first = document(
            "11111111-1111-4111-8111-111111111111", "tenant-1", "user-1", "chat-1"
        )
        second = document(
            "22222222-2222-4222-8222-222222222222", "tenant-2", "user-2", "chat-2"
        )
        index.upsert(
            first,
            [StoredDocumentChunk(first.id, 0, None, "Failover uses a replica.", 5)],
        )
        index.upsert(
            second,
            [StoredDocumentChunk(second.id, 0, None, "Failover is private.", 4)],
        )
        results = index.search(
            "failover", "tenant-1", "user-1", "chat-1", limit=10
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].document_id, first.id)
        self.assertEqual(results[0].source_id, f"upload:{first.id}:0")

        index.delete_conversation("tenant-1", "user-1", "chat-1")
        self.assertEqual(
            index.search("failover", "tenant-1", "user-1", "chat-1", limit=10),
            [],
        )
        self.assertEqual(
            len(index.search("failover", "tenant-2", "user-2", "chat-2", limit=10)),
            1,
        )


if __name__ == "__main__":
    unittest.main()
