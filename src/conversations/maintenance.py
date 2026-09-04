"""Dry-run-first retention maintenance for conversations and auth sessions."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from src.auth.repository import PostgresAuthRepository
from src.config.settings import ApplicationSettings, ConversationSettings
from src.indexing.qdrant_index import make_client

from .memory import NullMemoryStore, QdrantMemoryStore
from .repository import PostgresConversationRepository
@dataclass(frozen=True)
class RetentionResult:
    cutoff: datetime
    discovered: int
    deleted: int
    changed_during_run: int
    auth_transactions_deleted: int
    auth_sessions_deleted: int
    dry_run: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "cutoff": self.cutoff.isoformat(),
            "discovered": self.discovered,
            "deleted": self.deleted,
            "changed_during_run": self.changed_during_run,
            "auth_transactions_deleted": self.auth_transactions_deleted,
            "auth_sessions_deleted": self.auth_sessions_deleted,
            "dry_run": self.dry_run,
        }


class ConversationRetentionJob:
    def __init__(self, repository, memory_store, auth_repository=None) -> None:
        self.repository = repository
        self.memory_store = memory_store
        self.auth_repository = auth_repository

    def run(
        self,
        *,
        cutoff: datetime,
        batch_size: int = 100,
        apply: bool = False,
    ) -> RetentionResult:
        if cutoff.tzinfo is None:
            raise ValueError("Retention cutoff must be timezone-aware.")
        if batch_size <= 0:
            raise ValueError("Retention batch size must be positive.")
        discovered = 0
        deleted = 0
        changed = 0
        if not apply:
            discovered = self.repository.count_expired_conversations(cutoff)
        else:
            while True:
                values = self.repository.list_expired_conversations(cutoff, batch_size)
                if not values:
                    break
                discovered += len(values)
                for value in values:
                    self.memory_store.delete_conversation(
                        value.tenant_id, value.user_id, value.id
                    )
                    removed = self.repository.delete_expired_conversation(
                        value.tenant_id, value.user_id, value.id, cutoff
                    )
                    deleted += int(removed)
                    changed += int(not removed)
                if len(values) < batch_size:
                    break
        auth_transactions = 0
        auth_sessions = 0
        if apply and self.auth_repository is not None:
            auth_transactions, auth_sessions = self.auth_repository.purge_expired(
                datetime.now(timezone.utc)
            )
        return RetentionResult(
            cutoff=cutoff,
            discovered=discovered,
            deleted=deleted,
            changed_during_run=changed,
            auth_transactions_deleted=auth_transactions,
            auth_sessions_deleted=auth_sessions,
            dry_run=not apply,
        )


def build_job() -> tuple[ConversationRetentionJob, ConversationSettings]:
    application_settings = ApplicationSettings.from_environment()
    settings = application_settings.conversation
    if settings.mode == "disabled" or not settings.postgres_dsn:
        raise RuntimeError("Conversation persistence is not enabled.")
    repository = PostgresConversationRepository(settings.postgres_dsn)
    memory_store: Any = NullMemoryStore()
    if settings.long_term_store == "qdrant":
        pipeline = application_settings.pipeline
        client = make_client(
            url=pipeline.qdrant_url,
            api_key=pipeline.qdrant_api_key,
            timeout=pipeline.qdrant_timeout_seconds,
        )
        memory_store = QdrantMemoryStore(
            client,
            None,
            query_prefix="",
            ensure_collection=False,
        )
    auth_repository = (
        PostgresAuthRepository(settings.postgres_dsn)
        if settings.mode == "oidc"
        else None
    )
    return ConversationRetentionJob(
        repository, memory_store, auth_repository
    ), settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply AVA conversation retention.")
    parser.add_argument("--apply", action="store_true", help="Delete eligible records.")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    job, settings = build_job()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
    result = job.run(cutoff=cutoff, batch_size=args.batch_size, apply=args.apply)
    print(json.dumps(result.as_dict(), indent=2))


if __name__ == "__main__":
    main()
