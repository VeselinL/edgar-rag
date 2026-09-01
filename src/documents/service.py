"""Conversation ownership, quota, extraction, and byte lifecycle for uploads."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from .extraction import extract_document
from .models import StoredDocumentChunk, UploadedDocument
from .repository import DocumentRepository
from .storage import FilesystemAssetStore


MAX_DOCUMENTS_PER_CHAT = 20
MAX_BYTES_PER_CHAT = 100 * 1024 * 1024


class DocumentQuotaError(ValueError):
    pass


class DocumentService:
    def __init__(
        self,
        repository: DocumentRepository,
        asset_store: FilesystemAssetStore,
        *,
        tenant_id: str,
        user_id: str,
        authorize_conversation: Callable[[str], object],
    ) -> None:
        self.repository = repository
        self.asset_store = asset_store
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.authorize_conversation = authorize_conversation

    def upload(
        self, conversation_id: str, filename: str, media_type: str, content: bytes
    ) -> UploadedDocument:
        self.authorize_conversation(conversation_id)
        existing = self.repository.list(self.tenant_id, self.user_id, conversation_id)
        if len(existing) >= MAX_DOCUMENTS_PER_CHAT:
            raise DocumentQuotaError("A chat can contain at most 20 uploaded documents.")
        if sum(item.size_bytes for item in existing) + len(content) > MAX_BYTES_PER_CHAT:
            raise DocumentQuotaError("Uploaded files in one chat are limited to 100 MiB.")
        extracted = extract_document(filename, media_type, content)
        document_id = str(uuid4())
        asset_key = str(uuid4())
        stored = self.asset_store.put(asset_key, content)
        now = datetime.now(timezone.utc)
        document = UploadedDocument(
            document_id,
            conversation_id,
            self.tenant_id,
            self.user_id,
            extracted.filename,
            extracted.media_type,
            stored.size_bytes,
            stored.sha256,
            asset_key,
            "ready",
            extracted.page_count,
            extracted.token_count,
            len(extracted.chunks),
            now,
            now,
        )
        chunks = [
            StoredDocumentChunk(
                document_id,
                chunk.ordinal,
                chunk.page_number,
                chunk.text,
                chunk.token_count,
            )
            for chunk in extracted.chunks
        ]
        try:
            return self.repository.create(document, chunks)
        except BaseException:
            self.asset_store.delete(asset_key)
            raise

    def list(self, conversation_id: str) -> list[UploadedDocument]:
        self.authorize_conversation(conversation_id)
        return self.repository.list(self.tenant_id, self.user_id, conversation_id)

    def chunks(self, conversation_id: str, document_id: str) -> list[StoredDocumentChunk]:
        self.authorize_conversation(conversation_id)
        return self.repository.chunks(
            self.tenant_id, self.user_id, conversation_id, document_id
        )

    def delete(self, conversation_id: str, document_id: str) -> None:
        self.authorize_conversation(conversation_id)
        document = self.repository.get(
            self.tenant_id, self.user_id, conversation_id, document_id
        )
        self.asset_store.delete(document.asset_key)
        self.repository.delete(
            self.tenant_id, self.user_id, conversation_id, document_id
        )
