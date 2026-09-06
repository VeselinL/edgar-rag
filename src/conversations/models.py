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
    # Empty means the complete fixed corpus; otherwise retrieval is restricted
    # to these validated tickers for the lifetime of this conversation.
    company_scope: tuple[str, ...] = ()


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
    conversation_id: str | None
    source_id: str | None
    memory_type: Literal["conversation_summary", "explicit"]
    content: str
    score: float = 0.0
    source_message_id: str | None = None
    version: int = 1
    deleted_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class UserPreferences:
    tenant_id: str
    user_id: str
    nickname: str = ""
    warmth: Literal["cold", "balanced", "warm"] = "balanced"
    enthusiasm: Literal["low", "balanced", "high"] = "balanced"
    emoji_use: Literal["off", "light"] = "off"
    custom_instructions: str = ""
    language: Literal["en", "sr"] = "en"
    model: str = "AZURE_GPT_4o_2024_1120"
    theme: Literal["light", "dark", "system"] = "system"
    memory_enabled: bool = True
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
