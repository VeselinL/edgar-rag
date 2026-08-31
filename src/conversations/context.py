"""Deterministic token-bounded short-term context and rebuildable summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import tiktoken

from .models import MemoryItem, Message, Summary
from .repository import ConversationRepository


SUMMARY_VERSION = 1
DEFAULT_ENCODING = "o200k_base"


@dataclass(frozen=True)
class ConversationContext:
    """Prompt context kept separate from filing evidence and source citations."""

    summary: str = ""
    recent_messages: tuple[Message, ...] = ()
    long_term_memories: tuple[MemoryItem, ...] = ()

    @property
    def short_term_ids(self) -> tuple[str, ...]:
        return tuple(message.id for message in self.recent_messages)

    @property
    def long_term_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.long_term_memories)

    def prompt_text(self) -> str:
        sections: list[str] = []
        if self.summary:
            sections.append("Rolling conversation summary (not filing evidence):\n" + self.summary)
        if self.long_term_memories:
            values = "\n".join(f"- {item.content}" for item in self.long_term_memories)
            sections.append("Relevant user memory (not filing evidence):\n" + values)
        if self.recent_messages:
            values = "\n".join(
                f"{message.role.title()}: {message.content}"
                for message in self.recent_messages
            )
            sections.append("Recent conversation turns (not filing evidence):\n" + values)
        return "\n\n".join(sections)


def _completed_turns(messages: Sequence[Message], excluded_turn_id: str | None) -> list[list[Message]]:
    turns: dict[str, list[Message]] = {}
    order: list[str] = []
    for message in messages:
        if message.client_turn_id == excluded_turn_id:
            continue
        if message.status != "completed":
            continue
        if message.client_turn_id not in turns:
            turns[message.client_turn_id] = []
            order.append(message.client_turn_id)
        turns[message.client_turn_id].append(message)
    return [
        sorted(turns[turn_id], key=lambda item: item.ordinal)
        for turn_id in order
        if {item.role for item in turns[turn_id]} == {"user", "assistant"}
    ]


class ConversationContextBuilder:
    """Select whole recent turns, then rebuild an extractive older-turn summary."""

    def __init__(
        self,
        repository: ConversationRepository,
        *,
        recent_token_budget: int = 2_048,
        summary_token_budget: int = 768,
        encoding_name: str = DEFAULT_ENCODING,
    ) -> None:
        if recent_token_budget <= 0 or summary_token_budget <= 0:
            raise ValueError("Conversation token budgets must be positive.")
        self.repository = repository
        self.recent_token_budget = recent_token_budget
        self.summary_token_budget = summary_token_budget
        self.encoding = tiktoken.get_encoding(encoding_name)

    def _tokens(self, value: str) -> int:
        return len(self.encoding.encode(value))

    def _turn_text(self, turn: Sequence[Message]) -> str:
        return "\n".join(f"{item.role.title()}: {item.content}" for item in turn)

    def _summary_text(self, turns: Sequence[Sequence[Message]]) -> str:
        if not turns:
            return ""
        # Keep extractive text so canonical company names, periods, user
        # constraints, unresolved questions, and cited IDs are never invented.
        selected: list[str] = []
        used = 0
        for turn in reversed(turns):
            text = self._turn_text(turn)
            cost = self._tokens(text)
            if used + cost > self.summary_token_budget:
                continue
            selected.append(text)
            used += cost
        return "\n".join(reversed(selected))

    def build(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        *,
        excluded_turn_id: str | None = None,
    ) -> tuple[ConversationContext, Summary | None]:
        messages = self.repository.list_messages(tenant_id, user_id, conversation_id)
        turns = _completed_turns(messages, excluded_turn_id)
        recent_reversed: list[list[Message]] = []
        used = 0
        for turn in reversed(turns):
            cost = self._tokens(self._turn_text(turn))
            if used + cost > self.recent_token_budget:
                continue
            recent_reversed.append(turn)
            used += cost
        recent_turns = list(reversed(recent_reversed))
        recent_ids = {item.id for turn in recent_turns for item in turn}
        older_turns = [
            turn for turn in turns if not any(item.id in recent_ids for item in turn)
        ]
        summary_text = self._summary_text(older_turns)
        summary: Summary | None = None
        if summary_text:
            through_ordinal = max(item.ordinal for turn in older_turns for item in turn)
            summary = self.repository.upsert_summary(
                tenant_id,
                user_id,
                conversation_id,
                through_ordinal,
                SUMMARY_VERSION,
                summary_text,
            )
        recent = tuple(item for turn in recent_turns for item in turn)
        return ConversationContext(summary=summary_text, recent_messages=recent), summary
