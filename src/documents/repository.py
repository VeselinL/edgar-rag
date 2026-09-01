"""Owner-filtered metadata and rebuildable chunk storage for uploads."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from .models import StoredDocumentChunk, UploadedDocument


class DocumentNotFoundError(LookupError):
    pass


class DuplicateDocumentError(ValueError):
    pass


class DocumentRepository(Protocol):
    def create(self, document: UploadedDocument, chunks: Sequence[StoredDocumentChunk]) -> UploadedDocument: ...
    def list(self, tenant_id: str, user_id: str, conversation_id: str) -> list[UploadedDocument]: ...
    def get(self, tenant_id: str, user_id: str, conversation_id: str, document_id: str) -> UploadedDocument: ...
    def chunks(self, tenant_id: str, user_id: str, conversation_id: str, document_id: str) -> list[StoredDocumentChunk]: ...
    def delete(self, tenant_id: str, user_id: str, conversation_id: str, document_id: str) -> UploadedDocument: ...


class InMemoryDocumentRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._documents: dict[str, UploadedDocument] = {}
        self._chunks: dict[str, list[StoredDocumentChunk]] = {}

    def health_check(self) -> bool:
        return True

    def create(
        self, document: UploadedDocument, chunks: Sequence[StoredDocumentChunk]
    ) -> UploadedDocument:
        with self._lock:
            if any(
                value.conversation_id == document.conversation_id
                and value.sha256 == document.sha256
                for value in self._documents.values()
            ):
                raise DuplicateDocumentError("This file is already attached to the chat.")
            self._documents[document.id] = document
            self._chunks[document.id] = deepcopy(list(chunks))
            return document

    def list(self, tenant_id: str, user_id: str, conversation_id: str) -> list[UploadedDocument]:
        with self._lock:
            return sorted(
                [
                    value
                    for value in self._documents.values()
                    if value.tenant_id == tenant_id
                    and value.user_id == user_id
                    and value.conversation_id == conversation_id
                ],
                key=lambda value: (value.created_at, value.id),
            )

    def get(self, tenant_id: str, user_id: str, conversation_id: str, document_id: str) -> UploadedDocument:
        with self._lock:
            value = self._documents.get(document_id)
            if (
                value is None
                or value.tenant_id != tenant_id
                or value.user_id != user_id
                or value.conversation_id != conversation_id
            ):
                raise DocumentNotFoundError("Uploaded document was not found.")
            return value

    def chunks(self, tenant_id: str, user_id: str, conversation_id: str, document_id: str) -> list[StoredDocumentChunk]:
        self.get(tenant_id, user_id, conversation_id, document_id)
        with self._lock:
            return deepcopy(self._chunks[document_id])

    def delete(self, tenant_id: str, user_id: str, conversation_id: str, document_id: str) -> UploadedDocument:
        with self._lock:
            value = self.get(tenant_id, user_id, conversation_id, document_id)
            del self._documents[document_id]
            self._chunks.pop(document_id, None)
            return value


class PostgresDocumentRepository:
    def __init__(self, dsn: str, *, auto_migrate: bool = True) -> None:
        if not dsn.strip():
            raise ValueError("A PostgreSQL DSN is required for uploaded documents.")
        self.dsn = dsn
        if auto_migrate:
            self.migrate()

    @staticmethod
    def _psycopg():
        import psycopg
        from psycopg import errors
        from psycopg.rows import dict_row

        return psycopg, errors, dict_row

    def _connect(self):
        psycopg, _, dict_row = self._psycopg()
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def migrate(self) -> None:
        with self._connect() as connection:
            migration = (
                Path(__file__).resolve().parents[1]
                / "conversations/migrations/0003_uploaded_documents.sql"
            )
            connection.execute(migration.read_text(encoding="utf-8"))

    def health_check(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 AS value").fetchone()["value"] == 1

    @staticmethod
    def _document(row: dict[str, Any]) -> UploadedDocument:
        return UploadedDocument(
            str(row["document_id"]),
            str(row["conversation_id"]),
            row["tenant_id"],
            row["user_id"],
            row["filename"],
            row["media_type"],
            row["size_bytes"],
            row["sha256"],
            str(row["asset_key"]),
            row["status"],
            row["page_count"],
            row["token_count"],
            row["chunk_count"],
            row["created_at"],
            row["updated_at"],
        )

    def create(self, document: UploadedDocument, chunks: Sequence[StoredDocumentChunk]) -> UploadedDocument:
        _, errors, _ = self._psycopg()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """INSERT INTO ava_uploaded_documents
                       (document_id, conversation_id, tenant_id, user_id, filename,
                        media_type, size_bytes, sha256, asset_key, status, page_count,
                        token_count, chunk_count)
                       SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                       WHERE EXISTS (
                         SELECT 1 FROM ava_conversations
                         WHERE conversation_id=%s AND tenant_id=%s AND user_id=%s
                           AND deleted_at IS NULL
                       ) RETURNING *""",
                    (
                        document.id, document.conversation_id, document.tenant_id,
                        document.user_id, document.filename, document.media_type,
                        document.size_bytes, document.sha256, document.asset_key,
                        document.status, document.page_count, document.token_count,
                        document.chunk_count, document.conversation_id,
                        document.tenant_id, document.user_id,
                    ),
                ).fetchone()
                if row is None:
                    raise DocumentNotFoundError("Conversation was not found.")
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """INSERT INTO ava_uploaded_document_chunks
                           (document_id, ordinal, page_number, text, token_count)
                           VALUES (%s,%s,%s,%s,%s)""",
                        [
                            (
                                chunk.document_id,
                                chunk.ordinal,
                                chunk.page_number,
                                chunk.text,
                                chunk.token_count,
                            )
                            for chunk in chunks
                        ],
                    )
            return self._document(row)
        except errors.UniqueViolation as error:
            raise DuplicateDocumentError("This file is already attached to the chat.") from error

    def list(self, tenant_id: str, user_id: str, conversation_id: str) -> list[UploadedDocument]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM ava_uploaded_documents
                   WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                   ORDER BY created_at, document_id""",
                (tenant_id, user_id, conversation_id),
            ).fetchall()
        return [self._document(row) for row in rows]

    def get(self, tenant_id: str, user_id: str, conversation_id: str, document_id: str) -> UploadedDocument:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM ava_uploaded_documents
                   WHERE document_id=%s AND conversation_id=%s AND tenant_id=%s AND user_id=%s""",
                (document_id, conversation_id, tenant_id, user_id),
            ).fetchone()
        if row is None:
            raise DocumentNotFoundError("Uploaded document was not found.")
        return self._document(row)

    def chunks(self, tenant_id: str, user_id: str, conversation_id: str, document_id: str) -> list[StoredDocumentChunk]:
        self.get(tenant_id, user_id, conversation_id, document_id)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM ava_uploaded_document_chunks
                   WHERE document_id=%s ORDER BY ordinal""",
                (document_id,),
            ).fetchall()
        return [
            StoredDocumentChunk(
                str(row["document_id"]), row["ordinal"], row["page_number"],
                row["text"], row["token_count"],
            )
            for row in rows
        ]

    def delete(self, tenant_id: str, user_id: str, conversation_id: str, document_id: str) -> UploadedDocument:
        value = self.get(tenant_id, user_id, conversation_id, document_id)
        with self._connect() as connection:
            row = connection.execute(
                """DELETE FROM ava_uploaded_documents
                   WHERE document_id=%s AND conversation_id=%s AND tenant_id=%s AND user_id=%s
                   RETURNING document_id""",
                (document_id, conversation_id, tenant_id, user_id),
            ).fetchone()
        if row is None:
            raise DocumentNotFoundError("Uploaded document was not found.")
        return value
