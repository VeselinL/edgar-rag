"""Backend-only records for conversation-scoped uploaded documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class UploadedDocument:
    id: str
    conversation_id: str
    tenant_id: str
    user_id: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    asset_key: str
    status: str
    page_count: int | None
    token_count: int
    chunk_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class StoredDocumentChunk:
    document_id: str
    ordinal: int
    page_number: int | None
    text: str
    token_count: int
