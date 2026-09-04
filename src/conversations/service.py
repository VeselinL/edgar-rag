"""Conversation lifecycle orchestration without FastAPI dependencies."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Sequence

from .context import ConversationContext, ConversationContextBuilder
from .memory import MemoryStore, NullMemoryStore
from .models import Conversation, MemoryItem, Message, StoredTurn
from .repository import ConversationRepository
from src.config.settings import ConversationSettings
from src.filings.corpus import ACTIVE_FILINGS


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
        document_factory: Any | None = None,
    ) -> None:
        self.repository = repository
        self.context_builder = context_builder
        self.memory_store = memory_store or NullMemoryStore()
        self.long_term_candidate_k = long_term_candidate_k
        self.long_term_score_threshold = long_term_score_threshold
        self.long_term_token_budget = long_term_token_budget
        self.document_factory = document_factory

    def for_owner(self, tenant_id: str, user_id: str) -> "ConversationService":
        if not tenant_id or not user_id:
            raise ValueError("Conversation ownership requires tenant and user IDs.")
        service = ConversationService(
            self.repository,
            tenant_id=tenant_id,
            user_id=user_id,
            context_builder=self.context_builder,
            memory_store=self.memory_store,
            long_term_candidate_k=self.long_term_candidate_k,
            long_term_score_threshold=self.long_term_score_threshold,
            long_term_token_budget=self.long_term_token_budget,
        )
        if self.document_factory is not None:
            service.document_lifecycle = self.document_factory.for_owner(service)
        return service


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
        document_lifecycle: Any | None = None,
    ) -> None:
        self.repository = repository
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.context_builder = context_builder or ConversationContextBuilder(repository)
        self.memory_store = memory_store or NullMemoryStore()
        self.long_term_candidate_k = long_term_candidate_k
        self.long_term_score_threshold = long_term_score_threshold
        self.long_term_token_budget = long_term_token_budget
        self.document_lifecycle = document_lifecycle

    def create(self, *, title: str = "New conversation", memory_enabled: bool = False, company_scope: Sequence[str] = ()) -> Conversation:
        clean_title = title.strip()[:120] or "New conversation"
        company_scope = self._validate_scope(company_scope)
        return self.repository.create_conversation(self.tenant_id, self.user_id, clean_title, memory_enabled, company_scope)

    @staticmethod
    def _validate_scope(company_scope: Sequence[str]) -> tuple[str, ...]:
        values = tuple(dict.fromkeys(str(item).strip().upper() for item in company_scope if str(item).strip()))
        unknown = set(values) - set(ACTIVE_FILINGS)
        if unknown:
            raise ValueError("Company scope contains an unsupported ticker.")
        return values

    def list(self) -> list[Conversation]:
        return self.repository.list_conversations(self.tenant_id, self.user_id)

    def get(self, conversation_id: str) -> Conversation:
        return self.repository.get_conversation(self.tenant_id, self.user_id, conversation_id)

    def update(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        memory_enabled: bool | None = None,
        pinned: bool | None = None,
        company_scope: Sequence[str] | None = None,
    ) -> Conversation:
        if title is not None:
            title = title.strip()[:120]
            if not title:
                raise ValueError("Conversation title cannot be empty.")
        if company_scope is not None:
            company_scope = self._validate_scope(company_scope)
        if memory_enabled is False:
            self.get(conversation_id)
            self.memory_store.delete_conversation(
                self.tenant_id, self.user_id, conversation_id
            )
        updated = self.repository.update_conversation(
            self.tenant_id, self.user_id, conversation_id,
            title=title, memory_enabled=memory_enabled, pinned=pinned,
            company_scope=company_scope,
        )
        if memory_enabled is True:
            self._sync_conversation_memory(conversation_id)
        return updated

    def delete(self, conversation_id: str) -> None:
        # Verify ownership first. Delete the derived index point before the
        # authoritative rows so a transient PostgreSQL failure remains
        # recoverable by rebuilding memory from the canonical transcript.
        self.get(conversation_id)
        if self.document_lifecycle is not None:
            self.document_lifecycle.delete_conversation(conversation_id)
        self.memory_store.delete_conversation(self.tenant_id, self.user_id, conversation_id)
        self.repository.delete_conversation(self.tenant_id, self.user_id, conversation_id)

    def submit_feedback(
        self,
        conversation_id: str,
        assistant_message_id: str,
        value: str,
        comment: str | None,
        answer_version: dict[str, Any],
    ) -> None:
        if value not in {"helpful", "not_helpful"}:
            raise ValueError("Feedback value must be helpful or not_helpful.")
        clean_comment = comment.strip()[:1000] if comment else None
        self.repository.submit_feedback(
            self.tenant_id,
            self.user_id,
            conversation_id,
            assistant_message_id,
            value,
            clean_comment or None,
            answer_version,
        )

    def delete_all(self) -> int:
        existing = self.list()
        if self.document_lifecycle is not None:
            for item in existing:
                self.delete(item.id)
            return len(existing)
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
        metadata = (
            dict(source_event)
            if "source_event" in source_event
            else {"source_event": source_event}
        )
        metadata.setdefault("used_source_ids", list(used_source_ids))
        message = self.repository.complete_turn(
            self.tenant_id,
            self.user_id,
            conversation_id,
            client_turn_id,
            answer,
            metadata,
            used_source_ids,
        )
        self._sync_conversation_memory(conversation_id)
        return message

    def fail_turn(self, conversation_id: str, client_turn_id: str) -> None:
        self.repository.fail_turn(self.tenant_id, self.user_id, conversation_id, client_turn_id)
