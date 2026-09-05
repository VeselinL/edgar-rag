"""Conversation repository contract with PostgreSQL and deterministic test storage."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from .models import Conversation, MemoryItem, Message, StoredTurn, Summary, UserPreferences, utc_now


class ConversationNotFoundError(LookupError):
    pass


class TurnConflictError(RuntimeError):
    pass


class ConversationRepository(Protocol):
    def create_conversation(self, tenant_id: str, user_id: str, title: str, memory_enabled: bool, company_scope: Sequence[str] = ()) -> Conversation: ...
    def list_conversations(self, tenant_id: str, user_id: str) -> list[Conversation]: ...
    def get_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> Conversation: ...
    def update_conversation(self, tenant_id: str, user_id: str, conversation_id: str, *, title: str | None = None, memory_enabled: bool | None = None, pinned: bool | None = None, company_scope: Sequence[str] | None = None) -> Conversation: ...
    def delete_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> bool: ...
    def delete_all_conversations(self, tenant_id: str, user_id: str) -> list[str]: ...
    def begin_turn(self, tenant_id: str, user_id: str, conversation_id: str, client_turn_id: str, query: str, request_id: str) -> StoredTurn: ...
    def complete_turn(self, tenant_id: str, user_id: str, conversation_id: str, client_turn_id: str, answer: str, metadata: dict[str, Any], used_source_ids: Sequence[str]) -> Message: ...
    def fail_turn(self, tenant_id: str, user_id: str, conversation_id: str, client_turn_id: str) -> None: ...
    def list_messages(self, tenant_id: str, user_id: str, conversation_id: str) -> list[Message]: ...
    def get_summary(self, tenant_id: str, user_id: str, conversation_id: str) -> Summary | None: ...
    def upsert_summary(self, tenant_id: str, user_id: str, conversation_id: str, through_ordinal: int, version: int, content: str) -> Summary: ...
    def count_expired_conversations(self, cutoff: datetime) -> int: ...
    def list_expired_conversations(self, cutoff: datetime, limit: int) -> list[Conversation]: ...
    def delete_expired_conversation(self, tenant_id: str, user_id: str, conversation_id: str, cutoff: datetime) -> bool: ...
    def submit_feedback(self, tenant_id: str, user_id: str, conversation_id: str, assistant_message_id: str, value: str, comment: str | None, answer_version: dict[str, Any]) -> None: ...
    def list_memory_items(self, tenant_id: str, user_id: str) -> list[MemoryItem]: ...
    def create_memory_item(self, tenant_id: str, user_id: str, content: str, memory_type: str, source_conversation_id: str | None = None, source_message_id: str | None = None) -> MemoryItem: ...
    def update_memory_item(self, tenant_id: str, user_id: str, memory_id: str, content: str) -> MemoryItem: ...
    def delete_memory_item(self, tenant_id: str, user_id: str, memory_id: str) -> MemoryItem: ...
    def upsert_conversation_summary_memory(self, tenant_id: str, user_id: str, conversation_id: str, source_message_id: str | None, content: str) -> MemoryItem: ...
    def get_preferences(self, tenant_id: str, user_id: str) -> UserPreferences: ...
    def upsert_preferences(self, tenant_id: str, user_id: str, **values: str) -> UserPreferences: ...


class InMemoryConversationRepository:
    """Thread-safe repository used only by tests and explicit mock mode."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._conversations: dict[str, Conversation] = {}
        self._messages: dict[str, list[Message]] = {}
        self._summaries: dict[str, Summary] = {}
        self._memory_items: dict[str, MemoryItem] = {}
        self._preferences: dict[tuple[str, str], UserPreferences] = {}
        self.deletion_audit: list[dict[str, Any]] = []
        self.feedback: dict[str, dict[str, Any]] = {}

    def health_check(self) -> bool:
        return True

    def create_conversation(self, tenant_id: str, user_id: str, title: str, memory_enabled: bool, company_scope: Sequence[str] = ()) -> Conversation:
        with self._lock:
            now = utc_now()
            item = Conversation(
                id=str(uuid4()), tenant_id=tenant_id, user_id=user_id,
                title=title, memory_enabled=memory_enabled, pinned=False,
                pinned_at=None, created_at=now, updated_at=now,
                company_scope=tuple(company_scope),
            )
            self._conversations[item.id] = item
            self._messages[item.id] = []
            return item

    def _owned(self, tenant_id: str, user_id: str, conversation_id: str) -> Conversation:
        item = self._conversations.get(conversation_id)
        if item is None or item.tenant_id != tenant_id or item.user_id != user_id:
            raise ConversationNotFoundError("Conversation was not found.")
        return item

    def list_conversations(self, tenant_id: str, user_id: str) -> list[Conversation]:
        with self._lock:
            return sorted(
                [item for item in self._conversations.values() if item.tenant_id == tenant_id and item.user_id == user_id],
                key=lambda item: (
                    item.pinned,
                    item.pinned_at or item.updated_at,
                    item.updated_at,
                    item.id,
                ),
                reverse=True,
            )

    def get_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> Conversation:
        with self._lock:
            return self._owned(tenant_id, user_id, conversation_id)

    def update_conversation(self, tenant_id: str, user_id: str, conversation_id: str, *, title: str | None = None, memory_enabled: bool | None = None, pinned: bool | None = None, company_scope: Sequence[str] | None = None) -> Conversation:
        with self._lock:
            current = self._owned(tenant_id, user_id, conversation_id)
            now = utc_now()
            item = Conversation(
                id=current.id,
                tenant_id=current.tenant_id,
                user_id=current.user_id,
                title=title if title is not None else current.title,
                memory_enabled=memory_enabled if memory_enabled is not None else current.memory_enabled,
                pinned=pinned if pinned is not None else current.pinned,
                pinned_at=(now if pinned else None) if pinned is not None else current.pinned_at,
                created_at=current.created_at,
                updated_at=now,
                company_scope=tuple(company_scope) if company_scope is not None else current.company_scope,
            )
            self._conversations[conversation_id] = item
            return item

    def delete_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        with self._lock:
            self._owned(tenant_id, user_id, conversation_id)
            del self._conversations[conversation_id]
            self._messages.pop(conversation_id, None)
            self._summaries.pop(conversation_id, None)
            self._memory_items = {
                key: item for key, item in self._memory_items.items()
                if item.conversation_id != conversation_id
            }
            self.deletion_audit.append({
                "tenant_id": tenant_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "scope": "conversation",
            })
            return True

    def delete_all_conversations(self, tenant_id: str, user_id: str) -> list[str]:
        with self._lock:
            ids = [item.id for item in self.list_conversations(tenant_id, user_id)]
            for conversation_id in ids:
                del self._conversations[conversation_id]
                self._messages.pop(conversation_id, None)
                self._summaries.pop(conversation_id, None)
                self._memory_items = {
                    key: item for key, item in self._memory_items.items()
                    if item.conversation_id != conversation_id
                }
            if ids:
                self.deletion_audit.append({
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "conversation_id": None,
                    "scope": "all_conversations",
                })
            return ids

    def list_expired_conversations(
        self, cutoff: datetime, limit: int
    ) -> list[Conversation]:
        if limit <= 0:
            raise ValueError("Retention batch limit must be positive.")
        with self._lock:
            return sorted(
                [value for value in self._conversations.values() if value.updated_at < cutoff],
                key=lambda value: (value.updated_at, value.id),
            )[:limit]

    def count_expired_conversations(self, cutoff: datetime) -> int:
        with self._lock:
            return sum(
                value.updated_at < cutoff
                for value in self._conversations.values()
            )

    def delete_expired_conversation(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        cutoff: datetime,
    ) -> bool:
        with self._lock:
            value = self._conversations.get(conversation_id)
            if (
                value is None
                or value.tenant_id != tenant_id
                or value.user_id != user_id
                or value.updated_at >= cutoff
            ):
                return False
            del self._conversations[conversation_id]
            self._messages.pop(conversation_id, None)
            self._summaries.pop(conversation_id, None)
            self._memory_items = {
                key: item for key, item in self._memory_items.items()
                if item.conversation_id != conversation_id
            }
            self.deletion_audit.append({
                "tenant_id": tenant_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "scope": "retention",
            })
            return True

    def begin_turn(self, tenant_id: str, user_id: str, conversation_id: str, client_turn_id: str, query: str, request_id: str) -> StoredTurn:
        with self._lock:
            conversation = self._owned(tenant_id, user_id, conversation_id)
            existing = [item for item in self._messages[conversation_id] if item.client_turn_id == client_turn_id]
            if existing:
                user = next(item for item in existing if item.role == "user")
                assistant = next(item for item in existing if item.role == "assistant")
                if user.content != query:
                    raise TurnConflictError("client_turn_id was already used for another query.")
                if assistant.status == "completed":
                    return StoredTurn(user, assistant, replay=True)
                if assistant.status == "in_progress":
                    raise TurnConflictError("This turn is already in progress.")
                restarted = Message(**{**assistant.__dict__, "status": "in_progress", "request_id": request_id})
                self._replace_message(restarted)
                return StoredTurn(user, restarted)
            ordinal = max((item.ordinal for item in self._messages[conversation_id]), default=0) + 1
            now = utc_now()
            user_message = Message(str(uuid4()), conversation_id, client_turn_id, "user", query, "completed", ordinal, now, request_id)
            assistant = Message(str(uuid4()), conversation_id, client_turn_id, "assistant", "", "in_progress", ordinal + 1, now, request_id)
            self._messages[conversation_id].extend([user_message, assistant])
            self._conversations[conversation_id] = Conversation(
                id=conversation.id,
                tenant_id=conversation.tenant_id,
                user_id=conversation.user_id,
                title=conversation.title if conversation.title != "New conversation" else query.strip()[:80],
                memory_enabled=conversation.memory_enabled,
                pinned=conversation.pinned,
                pinned_at=conversation.pinned_at,
                created_at=conversation.created_at,
                updated_at=now,
                company_scope=conversation.company_scope,
            )
            return StoredTurn(user_message, assistant)

    def _replace_message(self, replacement: Message) -> None:
        values = self._messages[replacement.conversation_id]
        self._messages[replacement.conversation_id] = [replacement if item.id == replacement.id else item for item in values]

    def complete_turn(self, tenant_id: str, user_id: str, conversation_id: str, client_turn_id: str, answer: str, metadata: dict[str, Any], used_source_ids: Sequence[str]) -> Message:
        with self._lock:
            self._owned(tenant_id, user_id, conversation_id)
            current = next(item for item in self._messages[conversation_id] if item.client_turn_id == client_turn_id and item.role == "assistant")
            completed = Message(**{**current.__dict__, "content": answer, "status": "completed", "metadata": deepcopy(metadata)})
            self._replace_message(completed)
            conversation = self._conversations[conversation_id]
            self._conversations[conversation_id] = Conversation(**{**conversation.__dict__, "updated_at": utc_now()})
            return completed

    def fail_turn(self, tenant_id: str, user_id: str, conversation_id: str, client_turn_id: str) -> None:
        with self._lock:
            self._owned(tenant_id, user_id, conversation_id)
            for item in self._messages[conversation_id]:
                if item.client_turn_id == client_turn_id and item.role == "assistant" and item.status == "in_progress":
                    self._replace_message(Message(**{**item.__dict__, "status": "failed"}))
                    return

    def list_messages(self, tenant_id: str, user_id: str, conversation_id: str) -> list[Message]:
        with self._lock:
            self._owned(tenant_id, user_id, conversation_id)
            return sorted(deepcopy(self._messages[conversation_id]), key=lambda item: item.ordinal)

    def get_summary(self, tenant_id: str, user_id: str, conversation_id: str) -> Summary | None:
        with self._lock:
            self._owned(tenant_id, user_id, conversation_id)
            return self._summaries.get(conversation_id)

    def upsert_summary(self, tenant_id: str, user_id: str, conversation_id: str, through_ordinal: int, version: int, content: str) -> Summary:
        with self._lock:
            self._owned(tenant_id, user_id, conversation_id)
            current = self._summaries.get(conversation_id)
            summary = Summary(current.id if current else str(uuid4()), conversation_id, through_ordinal, version, content, utc_now())
            self._summaries[conversation_id] = summary
            return summary

    def submit_feedback(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        assistant_message_id: str,
        value: str,
        comment: str | None,
        answer_version: dict[str, Any],
    ) -> None:
        with self._lock:
            self._owned(tenant_id, user_id, conversation_id)
            message = next(
                (
                    item for item in self._messages.get(conversation_id, [])
                    if item.id == assistant_message_id
                    and item.role == "assistant"
                    and item.status == "completed"
                ),
                None,
            )
            if message is None:
                raise ConversationNotFoundError("Assistant response was not found.")
            self.feedback[assistant_message_id] = {
                "value": value,
                "comment": comment,
                "source_ids": list(message.metadata.get("used_source_ids", [])),
                "answer_version": deepcopy(
                    message.metadata.get("answer_version", answer_version)
                ),
            }

    def list_memory_items(self, tenant_id: str, user_id: str) -> list[MemoryItem]:
        with self._lock:
            return sorted(
                [item for item in self._memory_items.values()
                 if item.tenant_id == tenant_id and item.user_id == user_id and item.deleted_at is None],
                key=lambda item: (item.updated_at, item.id), reverse=True,
            )

    def create_memory_item(
        self, tenant_id: str, user_id: str, content: str, memory_type: str,
        source_conversation_id: str | None = None, source_message_id: str | None = None,
    ) -> MemoryItem:
        if memory_type not in {"explicit", "conversation_summary"}:
            raise ValueError("Invalid memory type.")
        now = utc_now()
        item = MemoryItem(
            id=str(uuid4()), tenant_id=tenant_id, user_id=user_id,
            conversation_id=source_conversation_id, source_id=source_message_id,
            source_message_id=source_message_id, memory_type=memory_type, content=content,
            created_at=now, updated_at=now,
        )
        with self._lock:
            self._memory_items[item.id] = item
        return item

    def _owned_memory(self, tenant_id: str, user_id: str, memory_id: str) -> MemoryItem:
        item = self._memory_items.get(memory_id)
        if item is None or item.tenant_id != tenant_id or item.user_id != user_id or item.deleted_at is not None:
            raise ConversationNotFoundError("Memory item was not found.")
        return item

    def update_memory_item(self, tenant_id: str, user_id: str, memory_id: str, content: str) -> MemoryItem:
        with self._lock:
            current = self._owned_memory(tenant_id, user_id, memory_id)
            item = MemoryItem(**{**current.__dict__, "content": content, "version": current.version + 1, "updated_at": utc_now()})
            self._memory_items[memory_id] = item
            return item

    def delete_memory_item(self, tenant_id: str, user_id: str, memory_id: str) -> MemoryItem:
        with self._lock:
            current = self._owned_memory(tenant_id, user_id, memory_id)
            item = MemoryItem(**{**current.__dict__, "deleted_at": utc_now(), "updated_at": utc_now()})
            self._memory_items[memory_id] = item
            return item

    def upsert_conversation_summary_memory(
        self, tenant_id: str, user_id: str, conversation_id: str,
        source_message_id: str | None, content: str,
    ) -> MemoryItem:
        with self._lock:
            existing = next((item for item in self._memory_items.values() if (
                item.tenant_id == tenant_id and item.user_id == user_id
                and item.conversation_id == conversation_id
                and item.memory_type == "conversation_summary" and item.deleted_at is None
            )), None)
            if existing is None:
                return self.create_memory_item(
                    tenant_id, user_id, content, "conversation_summary",
                    conversation_id, source_message_id,
                )
            item = MemoryItem(**{
                **existing.__dict__, "content": content,
                "source_id": source_message_id, "source_message_id": source_message_id,
                "version": existing.version + 1, "updated_at": utc_now(),
            })
            self._memory_items[item.id] = item
            return item

    def get_preferences(self, tenant_id: str, user_id: str) -> UserPreferences:
        with self._lock:
            return self._preferences.get((tenant_id, user_id), UserPreferences(tenant_id, user_id))

    def upsert_preferences(self, tenant_id: str, user_id: str, **values: str) -> UserPreferences:
        with self._lock:
            current = self.get_preferences(tenant_id, user_id)
            item = UserPreferences(**{**current.__dict__, **values, "updated_at": utc_now()})
            self._preferences[(tenant_id, user_id)] = item
            return item


class PostgresConversationRepository:
    """PostgreSQL source of truth with owner filtering on every operation."""

    def __init__(self, dsn: str, *, auto_migrate: bool = True) -> None:
        if not dsn.strip():
            raise ValueError("A PostgreSQL DSN is required.")
        self.dsn = dsn
        if auto_migrate:
            self.migrate()

    @staticmethod
    def _psycopg():
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:
            raise RuntimeError("PostgreSQL history requires psycopg.") from error
        return psycopg, dict_row

    def _connect(self):
        psycopg, dict_row = self._psycopg()
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def health_check(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 AS value").fetchone()["value"] == 1

    def migrate(self) -> None:
        with self._connect() as connection:
            migrations = Path(__file__).with_name("migrations")
            for migration in sorted(migrations.glob("*.sql")):
                connection.execute(migration.read_text(encoding="utf-8"))

    def ensure_identity(self, tenant_id: str, user_id: str) -> None:
        with self._connect() as connection:
            connection.execute("INSERT INTO ava_tenants (tenant_id) VALUES (%s) ON CONFLICT DO NOTHING", (tenant_id,))
            connection.execute("INSERT INTO ava_users (tenant_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (tenant_id, user_id))

    @staticmethod
    def _conversation(row: dict[str, Any]) -> Conversation:
        return Conversation(
            id=str(row["conversation_id"]),
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            title=row["title"],
            memory_enabled=row["memory_enabled"],
            pinned=row["pinned"],
            pinned_at=row["pinned_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            company_scope=tuple(row.get("company_scope") or ()) if isinstance(row, dict) else tuple(row["company_scope"] or ()),
        )

    @staticmethod
    def _message(row: dict[str, Any]) -> Message:
        return Message(str(row["message_id"]), str(row["conversation_id"]), str(row["client_turn_id"]), row["role"], row["content"], row["status"], row["ordinal"], row["created_at"], str(row["request_id"]) if row["request_id"] else None, row["metadata"] or {})

    @staticmethod
    def _memory_item(row: dict[str, Any]) -> MemoryItem:
        return MemoryItem(
            id=str(row["memory_id"]), tenant_id=row["tenant_id"], user_id=row["user_id"],
            conversation_id=str(row["source_conversation_id"]) if row["source_conversation_id"] else None,
            source_id=str(row["source_message_id"]) if row["source_message_id"] else None,
            source_message_id=str(row["source_message_id"]) if row["source_message_id"] else None,
            memory_type=row["memory_type"], content=row["content"], version=row["version"],
            created_at=row["created_at"], updated_at=row["updated_at"], deleted_at=row["deleted_at"],
        )

    @staticmethod
    def _preferences(row: dict[str, Any]) -> UserPreferences:
        return UserPreferences(
            tenant_id=row["tenant_id"], user_id=row["user_id"], nickname=row["nickname"],
            warmth=row["warmth"], enthusiasm=row["enthusiasm"], emoji_use=row["emoji_use"],
            custom_instructions=row["custom_instructions"], language=row["language"],
            model=row["model"], theme=row["theme"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def create_conversation(self, tenant_id: str, user_id: str, title: str, memory_enabled: bool, company_scope: Sequence[str] = ()) -> Conversation:
        self.ensure_identity(tenant_id, user_id)
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO ava_conversations (conversation_id, tenant_id, user_id, title, memory_enabled, company_scope)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
                (str(uuid4()), tenant_id, user_id, title, memory_enabled, list(company_scope)),
            ).fetchone()
        return self._conversation(row)

    def list_conversations(self, tenant_id: str, user_id: str) -> list[Conversation]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM ava_conversations WHERE tenant_id=%s AND user_id=%s AND deleted_at IS NULL
                   ORDER BY pinned DESC, pinned_at DESC NULLS LAST, updated_at DESC, conversation_id DESC""", (tenant_id, user_id)
            ).fetchall()
        return [self._conversation(row) for row in rows]

    def get_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> Conversation:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ava_conversations WHERE conversation_id=%s AND tenant_id=%s AND user_id=%s AND deleted_at IS NULL",
                (conversation_id, tenant_id, user_id),
            ).fetchone()
        if row is None:
            raise ConversationNotFoundError("Conversation was not found.")
        return self._conversation(row)

    def update_conversation(self, tenant_id: str, user_id: str, conversation_id: str, *, title: str | None = None, memory_enabled: bool | None = None, pinned: bool | None = None, company_scope: Sequence[str] | None = None) -> Conversation:
        current = self.get_conversation(tenant_id, user_id, conversation_id)
        next_pinned = pinned if pinned is not None else current.pinned
        next_pinned_at = (
            utc_now() if pinned is True
            else None if pinned is False
            else current.pinned_at
        )
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE ava_conversations
                   SET title=%s, memory_enabled=%s, pinned=%s, pinned_at=%s, company_scope=%s, updated_at=NOW()
                   WHERE conversation_id=%s AND tenant_id=%s AND user_id=%s AND deleted_at IS NULL RETURNING *""",
                (
                    title if title is not None else current.title,
                    memory_enabled if memory_enabled is not None else current.memory_enabled,
                    next_pinned,
                    next_pinned_at,
                    list(company_scope) if company_scope is not None else list(current.company_scope),
                    conversation_id,
                    tenant_id,
                    user_id,
                ),
            ).fetchone()
        if row is None:
            raise ConversationNotFoundError("Conversation was not found.")
        return self._conversation(row)

    def delete_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT conversation_id FROM ava_conversations WHERE conversation_id=%s AND tenant_id=%s AND user_id=%s FOR UPDATE",
                (conversation_id, tenant_id, user_id),
            ).fetchone()
            if row is None:
                raise ConversationNotFoundError("Conversation was not found.")
            connection.execute(
                "INSERT INTO ava_deletion_audit (deletion_id, tenant_id, user_id, conversation_id, scope) VALUES (%s,%s,%s,%s,'conversation')",
                (str(uuid4()), tenant_id, user_id, conversation_id),
            )
            connection.execute(
                "DELETE FROM ava_conversations WHERE conversation_id=%s AND tenant_id=%s AND user_id=%s",
                (conversation_id, tenant_id, user_id),
            )
        return True

    def delete_all_conversations(self, tenant_id: str, user_id: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT conversation_id FROM ava_conversations WHERE tenant_id=%s AND user_id=%s FOR UPDATE",
                (tenant_id, user_id),
            ).fetchall()
            if rows:
                connection.execute(
                    "INSERT INTO ava_deletion_audit (deletion_id, tenant_id, user_id, scope) VALUES (%s,%s,%s,'all_conversations')",
                    (str(uuid4()), tenant_id, user_id),
                )
                connection.execute(
                    "DELETE FROM ava_conversations WHERE tenant_id=%s AND user_id=%s",
                    (tenant_id, user_id),
                )
        return [str(row["conversation_id"]) for row in rows]

    def begin_turn(self, tenant_id: str, user_id: str, conversation_id: str, client_turn_id: str, query: str, request_id: str) -> StoredTurn:
        with self._connect() as connection:
            owner = connection.execute(
                "SELECT * FROM ava_conversations WHERE conversation_id=%s AND tenant_id=%s AND user_id=%s AND deleted_at IS NULL FOR UPDATE",
                (conversation_id, tenant_id, user_id),
            ).fetchone()
            if owner is None:
                raise ConversationNotFoundError("Conversation was not found.")
            rows = connection.execute(
                "SELECT * FROM ava_messages WHERE conversation_id=%s AND client_turn_id=%s ORDER BY ordinal",
                (conversation_id, client_turn_id),
            ).fetchall()
            if rows:
                messages = [self._message(row) for row in rows]
                user = next(item for item in messages if item.role == "user")
                assistant = next(item for item in messages if item.role == "assistant")
                if user.content != query:
                    raise TurnConflictError("client_turn_id was already used for another query.")
                if assistant.status == "completed":
                    return StoredTurn(user, assistant, replay=True)
                if assistant.status == "in_progress":
                    raise TurnConflictError("This turn is already in progress.")
                row = connection.execute(
                    "UPDATE ava_messages SET status='in_progress', request_id=%s, updated_at=NOW() WHERE message_id=%s RETURNING *",
                    (request_id, assistant.id),
                ).fetchone()
                return StoredTurn(user, self._message(row))
            ordinal = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) AS value FROM ava_messages WHERE conversation_id=%s", (conversation_id,)
            ).fetchone()["value"] + 1
            user_row = connection.execute(
                """INSERT INTO ava_messages (message_id, conversation_id, client_turn_id, role, content, status, ordinal, request_id)
                   VALUES (%s,%s,%s,'user',%s,'completed',%s,%s) RETURNING *""",
                (str(uuid4()), conversation_id, client_turn_id, query, ordinal, request_id),
            ).fetchone()
            assistant_row = connection.execute(
                """INSERT INTO ava_messages (message_id, conversation_id, client_turn_id, role, status, ordinal, request_id)
                   VALUES (%s,%s,%s,'assistant','in_progress',%s,%s) RETURNING *""",
                (str(uuid4()), conversation_id, client_turn_id, ordinal + 1, request_id),
            ).fetchone()
            title = owner["title"] if owner["title"] != "New conversation" else query.strip()[:80]
            connection.execute("UPDATE ava_conversations SET title=%s, updated_at=NOW() WHERE conversation_id=%s", (title, conversation_id))
        return StoredTurn(self._message(user_row), self._message(assistant_row))

    def complete_turn(self, tenant_id: str, user_id: str, conversation_id: str, client_turn_id: str, answer: str, metadata: dict[str, Any], used_source_ids: Sequence[str]) -> Message:
        self.get_conversation(tenant_id, user_id, conversation_id)
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE ava_messages SET content=%s, status='completed', metadata=%s::jsonb, updated_at=NOW()
                   WHERE conversation_id=%s AND client_turn_id=%s AND role='assistant' RETURNING *""",
                (answer, json.dumps(metadata), conversation_id, client_turn_id),
            ).fetchone()
            if row is None:
                raise TurnConflictError("The turn does not exist.")
            for order, source_id in enumerate(dict.fromkeys(used_source_ids)):
                connection.execute(
                    """INSERT INTO ava_source_uses (conversation_id, assistant_message_id, source_id, source_order)
                       VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (conversation_id, row["message_id"], source_id, order),
                )
            connection.execute("UPDATE ava_conversations SET updated_at=NOW() WHERE conversation_id=%s", (conversation_id,))
        return self._message(row)

    def fail_turn(self, tenant_id: str, user_id: str, conversation_id: str, client_turn_id: str) -> None:
        self.get_conversation(tenant_id, user_id, conversation_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE ava_messages SET status='failed', updated_at=NOW() WHERE conversation_id=%s AND client_turn_id=%s AND role='assistant' AND status='in_progress'",
                (conversation_id, client_turn_id),
            )

    def list_messages(self, tenant_id: str, user_id: str, conversation_id: str) -> list[Message]:
        self.get_conversation(tenant_id, user_id, conversation_id)
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM ava_messages WHERE conversation_id=%s ORDER BY ordinal", (conversation_id,)).fetchall()
        return [self._message(row) for row in rows]

    def get_summary(self, tenant_id: str, user_id: str, conversation_id: str) -> Summary | None:
        self.get_conversation(tenant_id, user_id, conversation_id)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM ava_conversation_summaries WHERE conversation_id=%s", (conversation_id,)).fetchone()
        return None if row is None else Summary(str(row["summary_id"]), str(row["conversation_id"]), row["through_ordinal"], row["version"], row["content"], row["updated_at"])

    def upsert_summary(self, tenant_id: str, user_id: str, conversation_id: str, through_ordinal: int, version: int, content: str) -> Summary:
        self.get_conversation(tenant_id, user_id, conversation_id)
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO ava_conversation_summaries (summary_id, conversation_id, through_ordinal, version, content)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (conversation_id) DO UPDATE SET through_ordinal=EXCLUDED.through_ordinal,
                     version=EXCLUDED.version, content=EXCLUDED.content, updated_at=NOW()
                   RETURNING *""",
                (str(uuid4()), conversation_id, through_ordinal, version, content),
            ).fetchone()
        return Summary(str(row["summary_id"]), str(row["conversation_id"]), row["through_ordinal"], row["version"], row["content"], row["updated_at"])

    def submit_feedback(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        assistant_message_id: str,
        value: str,
        comment: str | None,
        answer_version: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            message = connection.execute(
                """SELECT m.message_id, m.metadata,
                          COALESCE(array_agg(s.source_id ORDER BY s.source_order)
                            FILTER (WHERE s.source_id IS NOT NULL), '{}') AS source_ids
                   FROM ava_messages m
                   JOIN ava_conversations c ON c.conversation_id=m.conversation_id
                   LEFT JOIN ava_source_uses s ON s.assistant_message_id=m.message_id
                   WHERE m.message_id=%s AND m.conversation_id=%s
                     AND m.role='assistant' AND m.status='completed'
                     AND c.tenant_id=%s AND c.user_id=%s AND c.deleted_at IS NULL
                   GROUP BY m.message_id, m.metadata""",
                (assistant_message_id, conversation_id, tenant_id, user_id),
            ).fetchone()
            if message is None:
                raise ConversationNotFoundError("Assistant response was not found.")
            metadata = {
                "source_ids": list(message["source_ids"]),
                "answer_version": message["metadata"].get(
                    "answer_version", answer_version
                ),
            }
            connection.execute(
                """INSERT INTO ava_feedback
                   (feedback_id, conversation_id, assistant_message_id, value, comment, answer_metadata)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb)
                   ON CONFLICT (assistant_message_id) DO UPDATE SET
                     value=EXCLUDED.value, comment=EXCLUDED.comment,
                     answer_metadata=EXCLUDED.answer_metadata, updated_at=NOW()""",
                (
                    str(uuid4()),
                    conversation_id,
                    assistant_message_id,
                    value,
                    comment,
                    json.dumps(metadata),
                ),
            )

    def list_memory_items(self, tenant_id: str, user_id: str) -> list[MemoryItem]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM ava_memory_items WHERE tenant_id=%s AND user_id=%s
                   AND deleted_at IS NULL ORDER BY updated_at DESC, memory_id DESC""",
                (tenant_id, user_id),
            ).fetchall()
        return [self._memory_item(row) for row in rows]

    def create_memory_item(
        self, tenant_id: str, user_id: str, content: str, memory_type: str,
        source_conversation_id: str | None = None, source_message_id: str | None = None,
    ) -> MemoryItem:
        if memory_type not in {"explicit", "conversation_summary"}:
            raise ValueError("Invalid memory type.")
        self.ensure_identity(tenant_id, user_id)
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO ava_memory_items
                   (memory_id, tenant_id, user_id, content, memory_type, source_conversation_id, source_message_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (str(uuid4()), tenant_id, user_id, content, memory_type, source_conversation_id, source_message_id),
            ).fetchone()
        return self._memory_item(row)

    def update_memory_item(self, tenant_id: str, user_id: str, memory_id: str, content: str) -> MemoryItem:
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE ava_memory_items SET content=%s, version=version+1, updated_at=NOW()
                   WHERE memory_id=%s AND tenant_id=%s AND user_id=%s AND deleted_at IS NULL RETURNING *""",
                (content, memory_id, tenant_id, user_id),
            ).fetchone()
        if row is None:
            raise ConversationNotFoundError("Memory item was not found.")
        return self._memory_item(row)

    def delete_memory_item(self, tenant_id: str, user_id: str, memory_id: str) -> MemoryItem:
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE ava_memory_items SET deleted_at=NOW(), updated_at=NOW()
                   WHERE memory_id=%s AND tenant_id=%s AND user_id=%s AND deleted_at IS NULL RETURNING *""",
                (memory_id, tenant_id, user_id),
            ).fetchone()
        if row is None:
            raise ConversationNotFoundError("Memory item was not found.")
        return self._memory_item(row)

    def upsert_conversation_summary_memory(
        self, tenant_id: str, user_id: str, conversation_id: str,
        source_message_id: str | None, content: str,
    ) -> MemoryItem:
        self.get_conversation(tenant_id, user_id, conversation_id)
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE ava_memory_items SET content=%s, source_message_id=%s,
                     version=version+1, updated_at=NOW()
                   WHERE tenant_id=%s AND user_id=%s AND source_conversation_id=%s
                     AND memory_type='conversation_summary' AND deleted_at IS NULL
                   RETURNING *""",
                (content, source_message_id, tenant_id, user_id, conversation_id),
            ).fetchone()
        if row is not None:
            return self._memory_item(row)
        return self.create_memory_item(
            tenant_id, user_id, content, "conversation_summary", conversation_id, source_message_id
        )

    def get_preferences(self, tenant_id: str, user_id: str) -> UserPreferences:
        self.ensure_identity(tenant_id, user_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ava_user_preferences WHERE tenant_id=%s AND user_id=%s",
                (tenant_id, user_id),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """INSERT INTO ava_user_preferences (tenant_id, user_id)
                       VALUES (%s,%s) RETURNING *""", (tenant_id, user_id)
                ).fetchone()
        return self._preferences(row)

    def upsert_preferences(self, tenant_id: str, user_id: str, **values: str) -> UserPreferences:
        current = self.get_preferences(tenant_id, user_id)
        allowed = {"nickname", "warmth", "enthusiasm", "emoji_use", "custom_instructions", "language", "model", "theme"}
        if not set(values) <= allowed:
            raise ValueError("Invalid preference field.")
        next_values = {**current.__dict__, **values}
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE ava_user_preferences SET nickname=%s, warmth=%s, enthusiasm=%s,
                     emoji_use=%s, custom_instructions=%s, language=%s, model=%s, theme=%s, updated_at=NOW()
                   WHERE tenant_id=%s AND user_id=%s RETURNING *""",
                (next_values["nickname"], next_values["warmth"], next_values["enthusiasm"],
                 next_values["emoji_use"], next_values["custom_instructions"], next_values["language"],
                 next_values["model"], next_values["theme"], tenant_id, user_id),
            ).fetchone()
        return self._preferences(row)

    def list_expired_conversations(
        self, cutoff: datetime, limit: int
    ) -> list[Conversation]:
        if limit <= 0:
            raise ValueError("Retention batch limit must be positive.")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM ava_conversations
                   WHERE deleted_at IS NULL AND updated_at<%s
                   ORDER BY updated_at, conversation_id
                   LIMIT %s""",
                (cutoff, limit),
            ).fetchall()
        return [self._conversation(row) for row in rows]

    def count_expired_conversations(self, cutoff: datetime) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS value FROM ava_conversations
                   WHERE deleted_at IS NULL AND updated_at<%s""",
                (cutoff,),
            ).fetchone()
        return int(row["value"])

    def delete_expired_conversation(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        cutoff: datetime,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT conversation_id FROM ava_conversations
                   WHERE conversation_id=%s AND tenant_id=%s AND user_id=%s
                     AND deleted_at IS NULL AND updated_at<%s
                   FOR UPDATE""",
                (conversation_id, tenant_id, user_id, cutoff),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                """INSERT INTO ava_deletion_audit
                   (deletion_id, tenant_id, user_id, conversation_id, scope)
                   VALUES (%s,%s,%s,%s,'retention')""",
                (str(uuid4()), tenant_id, user_id, conversation_id),
            )
            connection.execute(
                """DELETE FROM ava_conversations
                   WHERE conversation_id=%s AND tenant_id=%s AND user_id=%s""",
                (conversation_id, tenant_id, user_id),
            )
        return True
