"""Isolated owner/chat-filtered vector index for uploaded document chunks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence
from uuid import UUID, uuid5

import numpy as np
from qdrant_client import QdrantClient, models

from src.indexing.qdrant_index import DENSE_VECTOR_NAME, VECTOR_DIMENSION

from .models import StoredDocumentChunk, UploadedDocument


DOCUMENT_COLLECTION = "ava_uploaded_documents_v1"
DOCUMENT_NAMESPACE = UUID("c458778d-0936-4a6d-98ec-5cbeb08beeb7")


@dataclass(frozen=True)
class DocumentEvidence:
    source_id: str
    document_id: str
    filename: str
    media_type: str
    page_number: int | None
    text: str
    score: float


class DocumentIndex(Protocol):
    def upsert(self, document: UploadedDocument, chunks: Sequence[StoredDocumentChunk]) -> None: ...
    def search(self, query: str, tenant_id: str, user_id: str, conversation_id: str, *, limit: int) -> list[DocumentEvidence]: ...
    def delete_document(self, tenant_id: str, user_id: str, conversation_id: str, document_id: str) -> None: ...
    def delete_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> None: ...


class NullDocumentIndex:
    def health_check(self) -> bool:
        return True

    def upsert(self, document: UploadedDocument, chunks: Sequence[StoredDocumentChunk]) -> None:
        return None

    def search(self, query: str, tenant_id: str, user_id: str, conversation_id: str, *, limit: int) -> list[DocumentEvidence]:
        return []

    def delete_document(self, tenant_id: str, user_id: str, conversation_id: str, document_id: str) -> None:
        return None

    def delete_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> None:
        return None


class QdrantDocumentIndex:
    def __init__(
        self,
        client: QdrantClient,
        embedder: Any,
        *,
        query_prefix: str,
        collection_name: str = DOCUMENT_COLLECTION,
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
                raise ValueError("The uploaded-document collection is incompatible.")
        for field in ("tenant_id", "user_id", "conversation_id", "document_id"):
            self.client.create_payload_index(
                collection_name=self.collection_name,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
                wait=True,
            )

    def health_check(self) -> bool:
        return bool(self.client.collection_exists(self.collection_name))

    def _embed(self, values: str | list[str], *, query: bool) -> np.ndarray:
        if self.embedder is None:
            raise RuntimeError("Uploaded-document retrieval requires an embedder.")
        if isinstance(values, str):
            values = [self.query_prefix + values if query else values]
        vectors = self.embedder.encode(values, normalize_embeddings=True)
        return np.asarray(vectors, dtype=np.float32)

    @staticmethod
    def _point_id(document_id: str, ordinal: int) -> str:
        return str(uuid5(DOCUMENT_NAMESPACE, f"{document_id}:{ordinal}"))

    def upsert(self, document: UploadedDocument, chunks: Sequence[StoredDocumentChunk]) -> None:
        if not chunks:
            raise ValueError("An uploaded document must contain chunks.")
        vectors = self._embed([chunk.text for chunk in chunks], query=False)
        if vectors.shape != (len(chunks), VECTOR_DIMENSION):
            raise ValueError("Uploaded-document embeddings have an invalid shape.")
        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                models.PointStruct(
                    id=self._point_id(document.id, chunk.ordinal),
                    vector={DENSE_VECTOR_NAME: vector.tolist()},
                    payload={
                        "source_id": f"upload:{document.id}:{chunk.ordinal}",
                        "document_id": document.id,
                        "tenant_id": document.tenant_id,
                        "user_id": document.user_id,
                        "conversation_id": document.conversation_id,
                        "filename": document.filename,
                        "media_type": document.media_type,
                        "page_number": chunk.page_number,
                        "ordinal": chunk.ordinal,
                        "text": chunk.text,
                        "schema_version": 1,
                    },
                )
                for chunk, vector in zip(chunks, vectors)
            ],
            wait=True,
        )

    @staticmethod
    def _filter(
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        document_id: str | None = None,
    ) -> models.Filter:
        values = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
        }
        if document_id is not None:
            values["document_id"] = document_id
        return models.Filter(
            must=[
                models.FieldCondition(key=key, match=models.MatchValue(value=value))
                for key, value in values.items()
            ]
        )

    def search(
        self,
        query: str,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        *,
        limit: int,
    ) -> list[DocumentEvidence]:
        if not 1 <= limit <= 20:
            raise ValueError("Uploaded-document search limit must be between 1 and 20.")
        vector = self._embed(query, query=True)[0]
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=vector.tolist(),
            using=DENSE_VECTOR_NAME,
            query_filter=self._filter(tenant_id, user_id, conversation_id),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        results = []
        for point in response.points:
            payload = point.payload or {}
            if any(
                payload.get(key) != expected
                for key, expected in {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                }.items()
            ):
                raise RuntimeError("Uploaded-document Qdrant filter leaked another owner or chat.")
            results.append(
                DocumentEvidence(
                    payload["source_id"],
                    payload["document_id"],
                    payload["filename"],
                    payload["media_type"],
                    payload.get("page_number"),
                    payload["text"],
                    float(point.score),
                )
            )
        return results

    def _delete(self, tenant_id: str, user_id: str, conversation_id: str, document_id: str | None = None) -> None:
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=self._filter(tenant_id, user_id, conversation_id, document_id)
            ),
            wait=True,
        )

    def delete_document(self, tenant_id: str, user_id: str, conversation_id: str, document_id: str) -> None:
        self._delete(tenant_id, user_id, conversation_id, document_id)

    def delete_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> None:
        self._delete(tenant_id, user_id, conversation_id)
