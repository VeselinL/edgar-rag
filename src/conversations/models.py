"""Backend-only conversation records independent of PostgreSQL and FastAPI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Conversation:
    id: str
    tenant_id: str
    user_id: str
    title: str
    memory_enabled: bool
    pinned: bool
    pinned_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Message:
    id: str
    conversation_id: str
    client_turn_id: str
    role: Literal["user", "assistant"]
    content: str
    status: Literal["in_progress", "completed", "failed"]
    ordinal: int
    created_at: datetime
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoredTurn:
    user_message: Message
    assistant_message: Message
    replay: bool = False


@dataclass(frozen=True)
class Summary:
    id: str
    conversation_id: str
    through_ordinal: int
    version: int
    content: str
    updated_at: datetime


@dataclass(frozen=True)
class MemoryItem:
    id: str
    tenant_id: str
    user_id: str
    conversation_id: str
    source_id: str
    memory_type: Literal["summary", "preference", "project_context"]
    content: str
    score: float = 0.0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
