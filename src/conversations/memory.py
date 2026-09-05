"""Tenant-filtered semantic long-term memory in a filing-independent collection."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Protocol
from uuid import UUID, uuid5

import numpy as np
from qdrant_client import QdrantClient, models

from src.indexing.qdrant_index import DENSE_VECTOR_NAME, VECTOR_DIMENSION

from .models import MemoryItem


MEMORY_COLLECTION = "ava_conversation_memory_v1"
MEMORY_NAMESPACE = UUID("45f56b4d-f276-46d8-bcae-f580acafca6a")


class MemoryStore(Protocol):
    def upsert(self, item: MemoryItem) -> None: ...
    def upsert_summary(self, item: MemoryItem) -> None: ...
    def search(self, query: str, tenant_id: str, user_id: str, *, limit: int, threshold: float, exclude_conversation_id: str | None = None) -> list[MemoryItem]: ...
    def delete_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> None: ...
    def delete_all(self, tenant_id: str, user_id: str) -> None: ...
    def delete_item(self, tenant_id: str, user_id: str, memory_id: str) -> None: ...


class NullMemoryStore:
    def health_check(self) -> bool:
        return True

    def upsert_summary(self, item: MemoryItem) -> None:
        return None

    def upsert(self, item: MemoryItem) -> None:
        return None

    def search(self, query: str, tenant_id: str, user_id: str, *, limit: int, threshold: float, exclude_conversation_id: str | None = None) -> list[MemoryItem]:
        return []

    def delete_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> None:
        return None

    def delete_all(self, tenant_id: str, user_id: str) -> None:
        return None

    def delete_item(self, tenant_id: str, user_id: str, memory_id: str) -> None:
        return None


class InMemoryMemoryStore:
    """Deterministic lexical stand-in for isolation and service tests."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.items: dict[str, MemoryItem] = {}

    def health_check(self) -> bool:
        return True

    def upsert_summary(self, item: MemoryItem) -> None:
        self.upsert(item)

    def upsert(self, item: MemoryItem) -> None:
        with self._lock:
            self.items[item.id] = item

    def search(self, query: str, tenant_id: str, user_id: str, *, limit: int, threshold: float, exclude_conversation_id: str | None = None) -> list[MemoryItem]:
        terms = set(query.casefold().split())
        ranked = []
        with self._lock:
            for item in self.items.values():
                if item.tenant_id != tenant_id or item.user_id != user_id:
                    continue
                if (
                    exclude_conversation_id
                    and item.conversation_id == exclude_conversation_id
                    and item.memory_type == "conversation_summary"
                ):
                    continue
                item_terms = set(item.content.casefold().split())
                score = len(terms & item_terms) / max(len(terms), 1)
                if score >= threshold:
                    ranked.append(MemoryItem(**{**item.__dict__, "score": score}))
        return sorted(ranked, key=lambda item: (-item.score, item.id))[:limit]

    def delete_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> None:
        with self._lock:
            self.items = {
                key: item for key, item in self.items.items()
                if not (item.tenant_id == tenant_id and item.user_id == user_id and item.conversation_id == conversation_id)
            }

    def delete_all(self, tenant_id: str, user_id: str) -> None:
        with self._lock:
            self.items = {
                key: item for key, item in self.items.items()
                if not (item.tenant_id == tenant_id and item.user_id == user_id)
            }

    def delete_item(self, tenant_id: str, user_id: str, memory_id: str) -> None:
        with self._lock:
            item = self.items.get(memory_id)
            if item is not None and item.tenant_id == tenant_id and item.user_id == user_id:
                del self.items[memory_id]


class QdrantMemoryStore:
    """Separate dense collection with mandatory tenant and user filters."""

    def __init__(
        self,
        client: QdrantClient,
        embedder: Any,
        *,
        query_prefix: str,
        collection_name: str = MEMORY_COLLECTION,
        ensure_collection: bool = True,
    ) -> None:
        self.client = client
        self.embedder = embedder
        self.query_prefix = query_prefix
        self.collection_name = collection_name
        if ensure_collection:
            self._ensure_collection()

    def _ensure_collection(self) -> None:
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    DENSE_VECTOR_NAME: models.VectorParams(
                        size=VECTOR_DIMENSION,
                        distance=models.Distance.COSINE,
                    )
                },
            )
        else:
            info = self.client.get_collection(self.collection_name)
            vectors = info.config.params.vectors
            config = vectors.get(DENSE_VECTOR_NAME) if isinstance(vectors, dict) else None
            if (
                config is None
                or config.size != VECTOR_DIMENSION
                or config.distance != models.Distance.COSINE
            ):
                raise ValueError("The existing conversation-memory collection is incompatible.")
        for field in ("tenant_id", "user_id", "conversation_id", "memory_type"):
            self.client.create_payload_index(
                collection_name=self.collection_name,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
                wait=True,
            )

    def health_check(self) -> bool:
        return bool(self.client.collection_exists(self.collection_name))

    def close(self) -> None:
        self.client.close()

    def _embed(self, value: str, *, query: bool) -> list[float]:
        if self.embedder is None:
            raise RuntimeError("This Qdrant memory store is configured for maintenance only.")
        prefix = self.query_prefix if query else ""
        vector = self.embedder.encode(prefix + value, normalize_embeddings=True)
        return np.asarray(vector, dtype=np.float32).tolist()

    @staticmethod
    def _point_id(memory_id: str) -> str:
        return str(uuid5(MEMORY_NAMESPACE, memory_id))

    def upsert_summary(self, item: MemoryItem) -> None:
        self.upsert(item)

    def upsert(self, item: MemoryItem) -> None:
        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                models.PointStruct(
                    id=self._point_id(item.id),
                    vector={DENSE_VECTOR_NAME: self._embed(item.content, query=False)},
                    payload={
                        "memory_id": item.id,
                        "tenant_id": item.tenant_id,
                        "user_id": item.user_id,
                        "conversation_id": item.conversation_id,
                        "source_id": item.source_id,
                        "source_message_id": item.source_message_id,
                        "memory_type": item.memory_type,
                        "content": item.content,
                        "created_at": item.created_at.isoformat(),
                        "updated_at": item.updated_at.isoformat(),
                        "version": item.version,
                        "model": "BAAI/bge-base-en-v1.5",
                        "schema_version": 1,
                    },
                )
            ],
            wait=True,
        )

    def _filter(self, tenant_id: str, user_id: str, conversation_id: str | None = None) -> models.Filter:
        must = [
            models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
            models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
        ]
        must_not = []
        if conversation_id:
            must_not.append(models.Filter(must=[
                models.FieldCondition(
                    key="conversation_id", match=models.MatchValue(value=conversation_id)
                ),
                models.FieldCondition(
                    key="memory_type", match=models.MatchValue(value="conversation_summary")
                ),
            ]))
        return models.Filter(must=must, must_not=must_not)

    def search(self, query: str, tenant_id: str, user_id: str, *, limit: int, threshold: float, exclude_conversation_id: str | None = None) -> list[MemoryItem]:
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=self._embed(query, query=True),
            using=DENSE_VECTOR_NAME,
            query_filter=self._filter(tenant_id, user_id, exclude_conversation_id),
            limit=limit,
            score_threshold=threshold,
            with_payload=True,
            with_vectors=False,
        )
        values = []
        for point in response.points:
            payload = point.payload or {}
            if payload.get("tenant_id") != tenant_id or payload.get("user_id") != user_id:
                raise RuntimeError("Qdrant memory filter leaked another owner.")
            values.append(
                MemoryItem(
                    id=payload["memory_id"],
                    tenant_id=payload["tenant_id"],
                    user_id=payload["user_id"],
                    conversation_id=payload["conversation_id"],
                    source_id=payload["source_id"],
                    memory_type=payload["memory_type"],
                    content=payload["content"],
                    score=float(point.score),
                    source_message_id=payload.get("source_message_id"),
                    version=int(payload.get("version", 1)),
                    created_at=datetime.fromisoformat(payload["created_at"]),
                    updated_at=datetime.fromisoformat(payload["updated_at"]),
                )
            )
        return values

    def _delete_filter(self, tenant_id: str, user_id: str, conversation_id: str | None = None) -> None:
        must = [
            models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
            models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
        ]
        if conversation_id:
            must.append(models.FieldCondition(key="conversation_id", match=models.MatchValue(value=conversation_id)))
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(filter=models.Filter(must=must)),
            wait=True,
        )

    def delete_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> None:
        self._delete_filter(tenant_id, user_id, conversation_id)

    def delete_all(self, tenant_id: str, user_id: str) -> None:
        self._delete_filter(tenant_id, user_id)

    def delete_item(self, tenant_id: str, user_id: str, memory_id: str) -> None:
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(filter=models.Filter(must=[
                models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
                models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
                models.FieldCondition(key="memory_id", match=models.MatchValue(value=memory_id)),
            ])),
            wait=True,
        )
