"""Conversation lifecycle orchestration without FastAPI dependencies."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import os
from typing import Any, Sequence

from .context import ConversationContext, ConversationContextBuilder
from .memory import MemoryStore, NullMemoryStore
from .models import Conversation, MemoryItem, Message, StoredTurn
from .repository import ConversationRepository


@dataclass(frozen=True)
class ConversationSettings:
    mode: str = "disabled"
    postgres_dsn: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    single_user_boundary_acknowledged: bool = False
    recent_token_budget: int = 2_048
    summary_token_budget: int = 768
    long_term_token_budget: int = 512
    long_term_candidate_k: int = 5
    long_term_score_threshold: float = 0.55
    retention_days: int = 90
    long_term_store: str = "disabled"

    @classmethod
    def from_environment(cls) -> "ConversationSettings":
        mode = os.getenv("AVA_CONVERSATION_MODE", "disabled").strip().casefold()
        if mode not in {"disabled", "single_user", "oidc"}:
            raise ValueError(
                "AVA_CONVERSATION_MODE must be 'disabled', 'single_user', or 'oidc'."
            )
        acknowledged = os.getenv("AVA_SINGLE_USER_BOUNDARY_ACKNOWLEDGED", "false").strip().casefold()
        if acknowledged not in {"true", "false"}:
            raise ValueError("AVA_SINGLE_USER_BOUNDARY_ACKNOWLEDGED must be true or false.")
        settings = cls(
            mode=mode,
            postgres_dsn=os.getenv("AVA_POSTGRES_DSN") or None,
            tenant_id=os.getenv("AVA_TENANT_ID") or None,
            user_id=os.getenv("AVA_USER_ID") or None,
            single_user_boundary_acknowledged=acknowledged == "true",
            recent_token_budget=int(os.getenv("AVA_SHORT_TERM_TOKEN_BUDGET", "2048")),
            summary_token_budget=int(os.getenv("AVA_SUMMARY_TOKEN_BUDGET", "768")),
            long_term_token_budget=int(os.getenv("AVA_LONG_TERM_TOKEN_BUDGET", "512")),
            long_term_candidate_k=int(os.getenv("AVA_LONG_TERM_CANDIDATE_K", "5")),
            long_term_score_threshold=float(os.getenv("AVA_LONG_TERM_SCORE_THRESHOLD", "0.55")),
            retention_days=int(os.getenv("AVA_CONVERSATION_RETENTION_DAYS", "90")),
            long_term_store=os.getenv("AVA_LONG_TERM_MEMORY_STORE", "disabled").strip().casefold(),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        values = (
            self.recent_token_budget,
            self.summary_token_budget,
            self.long_term_token_budget,
            self.long_term_candidate_k,
            self.retention_days,
        )
        if any(value <= 0 for value in values):
            raise ValueError("Conversation budgets and retention must be positive.")
        if not 0 <= self.long_term_score_threshold <= 1:
            raise ValueError("AVA_LONG_TERM_SCORE_THRESHOLD must be between zero and one.")
        if self.long_term_store not in {"disabled", "qdrant"}:
            raise ValueError("AVA_LONG_TERM_MEMORY_STORE must be 'disabled' or 'qdrant'.")
        if self.mode == "single_user" and (
            not self.postgres_dsn
            or not self.tenant_id
            or not self.user_id
            or not self.single_user_boundary_acknowledged
        ):
            raise ValueError(
                "Single-user history requires AVA_POSTGRES_DSN, AVA_TENANT_ID, "
                "AVA_USER_ID, and AVA_SINGLE_USER_BOUNDARY_ACKNOWLEDGED=true."
            )
        if self.mode == "oidc" and not self.postgres_dsn:
            raise ValueError("OIDC conversation history requires AVA_POSTGRES_DSN.")


class ConversationServiceFactory:
    """Create lightweight owner-bound services over shared authoritative stores."""

    def __init__(
        self,
        repository: ConversationRepository,
        *,
        context_builder: ConversationContextBuilder,
        memory_store: MemoryStore | None = None,
        long_term_candidate_k: int = 5,
        long_term_score_threshold: float = 0.55,
        long_term_token_budget: int = 512,
    ) -> None:
        self.repository = repository
        self.context_builder = context_builder
        self.memory_store = memory_store or NullMemoryStore()
        self.long_term_candidate_k = long_term_candidate_k
        self.long_term_score_threshold = long_term_score_threshold
        self.long_term_token_budget = long_term_token_budget

    def for_owner(self, tenant_id: str, user_id: str) -> "ConversationService":
        if not tenant_id or not user_id:
            raise ValueError("Conversation ownership requires tenant and user IDs.")
        return ConversationService(
            self.repository,
            tenant_id=tenant_id,
            user_id=user_id,
            context_builder=self.context_builder,
            memory_store=self.memory_store,
            long_term_candidate_k=self.long_term_candidate_k,
            long_term_score_threshold=self.long_term_score_threshold,
            long_term_token_budget=self.long_term_token_budget,
        )


class ConversationService:
    """Own idempotent turns, context construction, memory, and deletion."""

    def __init__(
        self,
        repository: ConversationRepository,
        *,
        tenant_id: str,
        user_id: str,
        context_builder: ConversationContextBuilder | None = None,
        memory_store: MemoryStore | None = None,
        long_term_candidate_k: int = 5,
        long_term_score_threshold: float = 0.55,
        long_term_token_budget: int = 512,
    ) -> None:
        self.repository = repository
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.context_builder = context_builder or ConversationContextBuilder(repository)
        self.memory_store = memory_store or NullMemoryStore()
        self.long_term_candidate_k = long_term_candidate_k
        self.long_term_score_threshold = long_term_score_threshold
        self.long_term_token_budget = long_term_token_budget

    def create(self, *, title: str = "New conversation", memory_enabled: bool = False) -> Conversation:
        clean_title = title.strip()[:120] or "New conversation"
        return self.repository.create_conversation(self.tenant_id, self.user_id, clean_title, memory_enabled)

    def list(self) -> list[Conversation]:
        return self.repository.list_conversations(self.tenant_id, self.user_id)

    def get(self, conversation_id: str) -> Conversation:
        return self.repository.get_conversation(self.tenant_id, self.user_id, conversation_id)

    def update(self, conversation_id: str, *, title: str | None = None, memory_enabled: bool | None = None) -> Conversation:
        if title is not None:
            title = title.strip()[:120]
            if not title:
                raise ValueError("Conversation title cannot be empty.")
        if memory_enabled is False:
            self.get(conversation_id)
            self.memory_store.delete_conversation(
                self.tenant_id, self.user_id, conversation_id
            )
        updated = self.repository.update_conversation(
            self.tenant_id, self.user_id, conversation_id,
            title=title, memory_enabled=memory_enabled,
        )
        if memory_enabled is True:
            self._sync_conversation_memory(conversation_id)
        return updated

    def delete(self, conversation_id: str) -> None:
        # Verify ownership first. Delete the derived index point before the
        # authoritative rows so a transient PostgreSQL failure remains
        # recoverable by rebuilding memory from the canonical transcript.
        self.get(conversation_id)
        self.memory_store.delete_conversation(self.tenant_id, self.user_id, conversation_id)
        self.repository.delete_conversation(self.tenant_id, self.user_id, conversation_id)

    def delete_all(self) -> int:
        existing = self.list()
        self.memory_store.delete_all(self.tenant_id, self.user_id)
        deleted = self.repository.delete_all_conversations(self.tenant_id, self.user_id)
        if len(deleted) != len(existing):
            raise RuntimeError("Conversation deletion count changed during the operation.")
        return len(deleted)

    def purge_expired(self, cutoff: datetime) -> int:
        """Delete owner-scoped conversations older than a configured cutoff."""
        if cutoff.tzinfo is None:
            raise ValueError("Retention cutoff must be timezone-aware.")
        expired = [item for item in self.list() if item.updated_at < cutoff]
        for item in expired:
            self.delete(item.id)
        return len(expired)

    def messages(self, conversation_id: str) -> list[Message]:
        return self.repository.list_messages(self.tenant_id, self.user_id, conversation_id)

    def begin_turn(self, conversation_id: str, client_turn_id: str, query: str, request_id: str) -> StoredTurn:
        return self.repository.begin_turn(
            self.tenant_id, self.user_id, conversation_id, client_turn_id, query, request_id
        )

    def prepare_context(self, conversation_id: str, client_turn_id: str, query: str) -> ConversationContext:
        conversation = self.get(conversation_id)
        context, summary = self.context_builder.build(
            self.tenant_id, self.user_id, conversation_id, excluded_turn_id=client_turn_id
        )
        if conversation.memory_enabled and summary is not None:
            now = datetime.now(timezone.utc)
            self.memory_store.upsert_summary(
                MemoryItem(
                    id=f"summary:{conversation_id}",
                    tenant_id=self.tenant_id,
                    user_id=self.user_id,
                    conversation_id=conversation_id,
                    source_id=summary.id,
                    memory_type="summary",
                    content=summary.content,
                    created_at=now,
                    updated_at=summary.updated_at,
                )
            )
        memories = ()
        if conversation.memory_enabled:
            candidates = self.memory_store.search(
                query,
                self.tenant_id,
                self.user_id,
                limit=self.long_term_candidate_k,
                threshold=self.long_term_score_threshold,
                exclude_conversation_id=conversation_id,
            )
            selected: list[MemoryItem] = []
            used_words = 0
            # Conservative deterministic approximation; prompt-level token
            # accounting still uses the exact formatter before evidence packing.
            for item in candidates:
                estimated = max(1, len(item.content.split()) * 4 // 3)
                if used_words + estimated > self.long_term_token_budget:
                    continue
                selected.append(item)
                used_words += estimated
            memories = tuple(selected)
        return replace(context, long_term_memories=memories)

    def _sync_conversation_memory(self, conversation_id: str) -> None:
        conversation = self.get(conversation_id)
        if not conversation.memory_enabled:
            return
        context, summary = self.context_builder.build(
            self.tenant_id, self.user_id, conversation_id
        )
        values = []
        if context.summary:
            values.append(context.summary)
        values.extend(
            f"{message.role.title()}: {message.content}"
            for message in context.recent_messages
        )
        content = "\n".join(values).strip()
        if not content:
            return
        now = datetime.now(timezone.utc)
        source_id = summary.id if summary is not None else context.recent_messages[-1].id
        self.memory_store.upsert_summary(
            MemoryItem(
                id=f"summary:{conversation_id}",
                tenant_id=self.tenant_id,
                user_id=self.user_id,
                conversation_id=conversation_id,
                source_id=source_id,
                memory_type="summary",
                content=content,
                created_at=now,
                updated_at=now,
            )
        )

    def complete_turn(self, conversation_id: str, client_turn_id: str, answer: str, source_event: dict[str, Any], used_source_ids: Sequence[str]) -> Message:
        message = self.repository.complete_turn(
            self.tenant_id,
            self.user_id,
            conversation_id,
            client_turn_id,
            answer,
            {"source_event": source_event},
            used_source_ids,
        )
        self._sync_conversation_memory(conversation_id)
        return message

    def fail_turn(self, conversation_id: str, client_turn_id: str) -> None:
        self.repository.fail_turn(self.tenant_id, self.user_id, conversation_id, client_turn_id)
