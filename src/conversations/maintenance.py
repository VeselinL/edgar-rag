"""Dry-run-first retention and canonical-memory maintenance for AVA."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from src.auth.repository import PostgresAuthRepository
from src.config.settings import ApplicationSettings, ConversationSettings
from src.embeddings.embed_chunks import MODEL_CONFIGS, load_model
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


@dataclass(frozen=True)
class MemoryReconciliationResult:
    tenant_id: str
    user_id: str
    indexed: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "indexed": self.indexed,
        }


class MemoryReconciliationJob:
    """Rebuild one owner's derived Qdrant memory index from PostgreSQL."""

    def __init__(self, repository, memory_store) -> None:
        self.repository = repository
        self.memory_store = memory_store

    def run(self, *, tenant_id: str, user_id: str) -> MemoryReconciliationResult:
        items = self.repository.list_memory_items(tenant_id, user_id)
        self.memory_store.delete_all(tenant_id, user_id)
        for item in items:
            self.memory_store.upsert(item)
        return MemoryReconciliationResult(tenant_id, user_id, len(items))


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


def build_reconciliation_job() -> MemoryReconciliationJob:
    application_settings = ApplicationSettings.from_environment()
    settings = application_settings.conversation
    if settings.mode == "disabled" or not settings.postgres_dsn:
        raise RuntimeError("Conversation persistence is not enabled.")
    if settings.long_term_store != "qdrant":
        raise RuntimeError("Qdrant long-term memory is not enabled.")
    pipeline = application_settings.pipeline
    local_path = (
        Path(pipeline.qdrant_local_path).expanduser().resolve()
        if pipeline.qdrant_local_path
        else None
    )
    client = make_client(
        url=None if local_path else pipeline.qdrant_url,
        api_key=pipeline.qdrant_api_key,
        local_path=local_path,
        timeout=pipeline.qdrant_timeout_seconds,
    )
    memory_store = QdrantMemoryStore(
        client,
        load_model("bgebase"),
        query_prefix=MODEL_CONFIGS["bgebase"]["query_prefix"],
    )
    return MemoryReconciliationJob(
        PostgresConversationRepository(settings.postgres_dsn), memory_store
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply AVA conversation maintenance.")
    parser.add_argument(
        "command", choices=("retention", "reconcile"), nargs="?", default="retention"
    )
    parser.add_argument("--apply", action="store_true", help="Delete eligible records.")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--tenant-id")
    parser.add_argument("--user-id")
    args = parser.parse_args()
    if args.command == "reconcile":
        if not args.tenant_id or not args.user_id:
            parser.error("reconcile requires --tenant-id and --user-id")
        result = build_reconciliation_job().run(
            tenant_id=args.tenant_id, user_id=args.user_id
        )
        print(json.dumps(result.as_dict(), indent=2))
        return
    job, settings = build_job()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
    result = job.run(cutoff=cutoff, batch_size=args.batch_size, apply=args.apply)
    print(json.dumps(result.as_dict(), indent=2))


if __name__ == "__main__":
    main()
