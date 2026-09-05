"""Conversation lifecycle orchestration without FastAPI dependencies."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import re
from typing import Any, Sequence

from .context import (
    ConversationContext,
    ConversationContextBuilder,
)
from .memory import MemoryStore, NullMemoryStore
from .models import Conversation, MemoryItem, Message, StoredTurn, UserPreferences
from .repository import ConversationNotFoundError, ConversationRepository
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

    _MAX_MEMORY_CONTENT_LENGTH = 1500
    _EXPLICIT_MEMORY_PREFIX = re.compile(
        r"^\s*(?:remember|save|zapamti|sačuvaj)(?:\s+(?:this|ovo|to))?"
        r"(?:\s+(?:in|to|u))?(?:\s+(?:long[- ]term\s+)?memory|memoriju)?\s*[:,-]?\s*",
        re.IGNORECASE,
    )
    _PREFERENCE_MEMORY_STATEMENT = re.compile(
        r"\b(?:i\s+(?:prefer|like|dislike|want|work|live)|my\s+(?:preferred|preference|name|"
        r"role|language)|call\s+me|answer\s+(?:in|with)|use\b|"
        r"prefer|avoid|keep\s+(?:answers?|responses?)|"
        r"ja\s+(?:preferiram|volim|ne\s+volim|želim|radim|živim)|moja?\s+(?:preferirana|"
        r"preferenca|ime|uloga|jezik)|zovi\s+me|odgovaraj\s+(?:na|sa)|koristi)\b",
        re.IGNORECASE,
    )
    _MEMORY_INSTRUCTION_PATTERN = re.compile(
        r"\b(?:ignore|disregard|override|system\s+prompt|developer(?:\s+message)?|"
        r"instructions?|tool|function|api\s*key|secret|execute|call\s+(?:a|the)\s+tool|"
        r"ignoriši|zanemari|zaobiđi|sistemski\s+(?:prompt|upit)|instrukcije?|"
        r"alat|funkcij\w*|api\s*(?:ključ|key)|tajna?)\b",
        re.IGNORECASE,
    )

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

    def create(self, *, title: str = "New conversation", memory_enabled: bool = True, company_scope: Sequence[str] = ()) -> Conversation:
        clean_title = title.strip()[:120] or "New conversation"
        company_scope = self._validate_scope(company_scope)
        # Kept as a compatibility argument for pre-Phase-6 callers. Normal
        # conversations always participate in long-term memory.
        return self.repository.create_conversation(self.tenant_id, self.user_id, clean_title, True, company_scope)

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
        if memory_enabled is not None:
            raise ValueError("Long-term memory is always enabled for normal chats.")
        updated = self.repository.update_conversation(
            self.tenant_id, self.user_id, conversation_id,
            title=title, memory_enabled=memory_enabled, pinned=pinned,
            company_scope=company_scope,
        )
        return updated

    @staticmethod
    def _clean_memory_content(content: str) -> str:
        value = content.strip()
        if not value:
            raise ValueError("Memory content cannot be empty.")
        if len(value) > ConversationService._MAX_MEMORY_CONTENT_LENGTH:
            raise ValueError("Memory content must be 1,500 characters or fewer.")
        return value

    @classmethod
    def _explicit_memory_content(cls, user_text: str) -> str | None:
        if not cls._EXPLICIT_MEMORY_PREFIX.match(user_text):
            return None
        content = cls._EXPLICIT_MEMORY_PREFIX.sub("", user_text.strip())
        return cls._validate_preference_memory(content)

    @classmethod
    def _validate_preference_memory(cls, content: str) -> str:
        value = cls._clean_memory_content(content)
        if cls._MEMORY_INSTRUCTION_PATTERN.search(value):
            raise ValueError("Memory cannot contain instructions, tools, secrets, or policy text.")
        if not cls._PREFERENCE_MEMORY_STATEMENT.search(value):
            raise ValueError("Memory must be a stable first-person preference or profile detail.")
        return value

    def list_memory(self) -> list[MemoryItem]:
        return self.repository.list_memory_items(self.tenant_id, self.user_id)

    def create_memory(self, content: str) -> MemoryItem:
        item = self.repository.create_memory_item(
            self.tenant_id, self.user_id, self._validate_preference_memory(content), "explicit"
        )
        self.memory_store.upsert(item)
        return item

    def update_memory(self, memory_id: str, content: str) -> MemoryItem:
        # Resolve ownership before content validation so this endpoint cannot
        # reveal validation behavior for another owner's memory item.
        if not any(item.id == memory_id for item in self.list_memory()):
            raise ConversationNotFoundError("Memory item was not found.")
        item = self.repository.update_memory_item(
            self.tenant_id, self.user_id, memory_id, self._validate_preference_memory(content)
        )
        self.memory_store.upsert(item)
        return item

    def delete_memory(self, memory_id: str) -> None:
        self.repository.delete_memory_item(self.tenant_id, self.user_id, memory_id)
        self.memory_store.delete_item(self.tenant_id, self.user_id, memory_id)

    def preferences(self) -> UserPreferences:
        return self.repository.get_preferences(self.tenant_id, self.user_id)

    def update_preferences(self, **values: str) -> UserPreferences:
        allowed = {
            "nickname", "warmth", "enthusiasm", "emoji_use", "custom_instructions",
            "language", "model", "theme",
        }
        if not values or not set(values) <= allowed:
            raise ValueError("Invalid preference field.")
        if "nickname" in values and len(values["nickname"].strip()) > 50:
            raise ValueError("Nickname must be 50 characters or fewer.")
        if "custom_instructions" in values and len(values["custom_instructions"].strip()) > 1500:
            raise ValueError("Custom instructions must be 1,500 characters or fewer.")
        enumerations = {
            "warmth": {"cold", "balanced", "warm"},
            "enthusiasm": {"low", "balanced", "high"},
            "emoji_use": {"off", "light"},
            "language": {"en", "sr"},
            "theme": {"light", "dark", "system"},
        }
        for name, valid in enumerations.items():
            if name in values and values[name] not in valid:
                raise ValueError(f"Invalid {name} preference.")
        return self.repository.upsert_preferences(
            self.tenant_id, self.user_id,
            **{key: value.strip() if isinstance(value, str) else value for key, value in values.items()},
        )

    @staticmethod
    def preference_prompt_fragment(preferences: UserPreferences) -> str:
        """Render only server-validated, lower-priority preference fields."""
        parts = [
            "Answer language: Serbian." if preferences.language == "sr" else "Answer language: English.",
            {
                "cold": "Use a concise, neutral professional tone.",
                "balanced": "Use a clear, measured professional tone.",
                "warm": "Use a friendly, considerate professional tone without becoming chatty.",
            }[preferences.warmth],
            {
                "low": "Use restrained language; avoid exclamation marks.",
                "balanced": "Use measured, matter-of-fact emphasis.",
                "high": "Use positive but financially professional emphasis; do not overstate evidence.",
            }[preferences.enthusiasm],
            (
                "A single restrained emoji is allowed only in a casual, non-factual response."
                if preferences.emoji_use == "light"
                else "Do not use emoji."
            ),
        ]
        if preferences.nickname:
            parts.append(
                f"User's name is {preferences.nickname}. Address the user by this name once, "
                "and no more than once, in every answer. It is not a company-resolution input."
            )
        if preferences.custom_instructions:
            parts.extend([
                "[BEGIN USER CUSTOMIZATION]",
                preferences.custom_instructions,
                "[END USER CUSTOMIZATION]",
            ])
        return "\n".join(parts)

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

    def prepare_context(
        self,
        conversation_id: str,
        client_turn_id: str,
        query: str,
        *,
        memory_query: str | None = None,
    ) -> ConversationContext:
        context, _ = self.context_builder.build(
            self.tenant_id, self.user_id, conversation_id, excluded_turn_id=client_turn_id
        )
        candidates = self.memory_store.search(
            memory_query or query,
            self.tenant_id,
            self.user_id,
            limit=self.long_term_candidate_k,
            threshold=self.long_term_score_threshold,
            exclude_conversation_id=conversation_id,
        )
        selected: list[MemoryItem] = []
        used_words = 0
        for item in candidates:
            estimated = max(1, len(item.content.split()) * 4 // 3)
            if used_words + estimated > self.long_term_token_budget:
                continue
            selected.append(item)
            used_words += estimated
        memories = tuple(selected)
        preferences = self.preferences()
        return replace(
            context,
            long_term_memories=memories,
            preference_text=self.preference_prompt_fragment(preferences),
            nickname=preferences.nickname,
            language=preferences.language,
        )

    def _sync_conversation_memory(self, conversation_id: str, client_turn_id: str) -> None:
        self.get(conversation_id)
        messages = self.messages(conversation_id)
        user_message = next(
            (
                message for message in messages
                if message.client_turn_id == client_turn_id and message.role == "user"
            ),
            None,
        )
        if user_message is not None:
            try:
                content = self._explicit_memory_content(user_message.content)
            except ValueError:
                content = None
            existing = {item.content.casefold() for item in self.list_memory()}
            if content is not None and content.casefold() not in existing:
                item = self.repository.create_memory_item(
                    self.tenant_id,
                    self.user_id,
                    content,
                    "explicit",
                    conversation_id,
                    user_message.id,
                )
                self.memory_store.upsert(item)
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
        self._sync_conversation_memory(conversation_id, client_turn_id)
        return message

    def fail_turn(self, conversation_id: str, client_turn_id: str) -> None:
        self.repository.fail_turn(self.tenant_id, self.user_id, conversation_id, client_turn_id)
