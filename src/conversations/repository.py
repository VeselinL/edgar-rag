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

from .models import Conversation, Message, StoredTurn, Summary, utc_now


class ConversationNotFoundError(LookupError):
    pass


class TurnConflictError(RuntimeError):
    pass


class ConversationRepository(Protocol):
    def create_conversation(self, tenant_id: str, user_id: str, title: str, memory_enabled: bool) -> Conversation: ...
    def list_conversations(self, tenant_id: str, user_id: str) -> list[Conversation]: ...
    def get_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> Conversation: ...
    def update_conversation(self, tenant_id: str, user_id: str, conversation_id: str, *, title: str | None = None, memory_enabled: bool | None = None) -> Conversation: ...
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


class InMemoryConversationRepository:
    """Thread-safe repository used only by tests and explicit mock mode."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._conversations: dict[str, Conversation] = {}
        self._messages: dict[str, list[Message]] = {}
        self._summaries: dict[str, Summary] = {}
        self.deletion_audit: list[dict[str, Any]] = []

    def health_check(self) -> bool:
        return True

    def create_conversation(self, tenant_id: str, user_id: str, title: str, memory_enabled: bool) -> Conversation:
        with self._lock:
            now = utc_now()
            item = Conversation(str(uuid4()), tenant_id, user_id, title, memory_enabled, now, now)
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
                key=lambda item: (item.updated_at, item.id),
                reverse=True,
            )

    def get_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> Conversation:
        with self._lock:
            return self._owned(tenant_id, user_id, conversation_id)

    def update_conversation(self, tenant_id: str, user_id: str, conversation_id: str, *, title: str | None = None, memory_enabled: bool | None = None) -> Conversation:
        with self._lock:
            current = self._owned(tenant_id, user_id, conversation_id)
            item = Conversation(
                current.id,
                current.tenant_id,
                current.user_id,
                title if title is not None else current.title,
                memory_enabled if memory_enabled is not None else current.memory_enabled,
                current.created_at,
                utc_now(),
            )
            self._conversations[conversation_id] = item
            return item

    def delete_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        with self._lock:
            self._owned(tenant_id, user_id, conversation_id)
            del self._conversations[conversation_id]
            self._messages.pop(conversation_id, None)
            self._summaries.pop(conversation_id, None)
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
                conversation.id, conversation.tenant_id, conversation.user_id,
                conversation.title if conversation.title != "New conversation" else query.strip()[:80],
                conversation.memory_enabled, conversation.created_at, now,
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
        migration = Path(__file__).with_name("migrations") / "0001_conversations.sql"
        with self._connect() as connection:
            connection.execute(migration.read_text(encoding="utf-8"))

    def ensure_identity(self, tenant_id: str, user_id: str) -> None:
        with self._connect() as connection:
            connection.execute("INSERT INTO ava_tenants (tenant_id) VALUES (%s) ON CONFLICT DO NOTHING", (tenant_id,))
            connection.execute("INSERT INTO ava_users (tenant_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (tenant_id, user_id))

    @staticmethod
    def _conversation(row: dict[str, Any]) -> Conversation:
        return Conversation(str(row["conversation_id"]), row["tenant_id"], row["user_id"], row["title"], row["memory_enabled"], row["created_at"], row["updated_at"])

    @staticmethod
    def _message(row: dict[str, Any]) -> Message:
        return Message(str(row["message_id"]), str(row["conversation_id"]), str(row["client_turn_id"]), row["role"], row["content"], row["status"], row["ordinal"], row["created_at"], str(row["request_id"]) if row["request_id"] else None, row["metadata"] or {})

    def create_conversation(self, tenant_id: str, user_id: str, title: str, memory_enabled: bool) -> Conversation:
        self.ensure_identity(tenant_id, user_id)
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO ava_conversations (conversation_id, tenant_id, user_id, title, memory_enabled)
                   VALUES (%s, %s, %s, %s, %s) RETURNING *""",
                (str(uuid4()), tenant_id, user_id, title, memory_enabled),
            ).fetchone()
        return self._conversation(row)

    def list_conversations(self, tenant_id: str, user_id: str) -> list[Conversation]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM ava_conversations WHERE tenant_id=%s AND user_id=%s AND deleted_at IS NULL
                   ORDER BY updated_at DESC, conversation_id DESC""", (tenant_id, user_id)
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

    def update_conversation(self, tenant_id: str, user_id: str, conversation_id: str, *, title: str | None = None, memory_enabled: bool | None = None) -> Conversation:
        current = self.get_conversation(tenant_id, user_id, conversation_id)
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE ava_conversations SET title=%s, memory_enabled=%s, updated_at=NOW()
                   WHERE conversation_id=%s AND tenant_id=%s AND user_id=%s AND deleted_at IS NULL RETURNING *""",
                (title if title is not None else current.title, memory_enabled if memory_enabled is not None else current.memory_enabled, conversation_id, tenant_id, user_id),
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
