"""Atomic OIDC transaction and opaque-session persistence."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Protocol

from .models import AuthSession, LoginTransaction
from .oidc import Principal


class AuthRepository(Protocol):
    def migrate(self) -> None: ...
    def create_transaction(self, value: LoginTransaction) -> None: ...
    def consume_transaction(self, state_hash: str, now: datetime) -> LoginTransaction | None: ...
    def create_session(self, value: AuthSession) -> None: ...
    def get_session(self, session_hash: str, now: datetime) -> AuthSession | None: ...
    def delete_session(self, session_hash: str) -> bool: ...
    def purge_expired(self, now: datetime) -> tuple[int, int]: ...


class InMemoryAuthRepository:
    """Deterministic auth storage used by tests only."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.transactions: dict[str, LoginTransaction] = {}
        self.sessions: dict[str, AuthSession] = {}

    def migrate(self) -> None:
        return None

    def health_check(self) -> bool:
        return True

    def create_transaction(self, value: LoginTransaction) -> None:
        with self._lock:
            self.transactions[value.state_hash] = value

    def consume_transaction(
        self, state_hash: str, now: datetime
    ) -> LoginTransaction | None:
        with self._lock:
            value = self.transactions.pop(state_hash, None)
            if value is None or value.expires_at <= now:
                return None
            return value

    def create_session(self, value: AuthSession) -> None:
        with self._lock:
            self.sessions[value.session_hash] = value

    def get_session(self, session_hash: str, now: datetime) -> AuthSession | None:
        with self._lock:
            value = self.sessions.get(session_hash)
            if value is None or value.expires_at <= now:
                self.sessions.pop(session_hash, None)
                return None
            return value

    def delete_session(self, session_hash: str) -> bool:
        with self._lock:
            return self.sessions.pop(session_hash, None) is not None

    def purge_expired(self, now: datetime) -> tuple[int, int]:
        with self._lock:
            transaction_ids = [
                key for key, value in self.transactions.items()
                if value.expires_at <= now
            ]
            session_ids = [
                key for key, value in self.sessions.items()
                if value.expires_at <= now
            ]
            for key in transaction_ids:
                del self.transactions[key]
            for key in session_ids:
                del self.sessions[key]
            return len(transaction_ids), len(session_ids)


class PostgresAuthRepository:
    """PostgreSQL session storage; raw cookie and state values are never stored."""

    def __init__(self, dsn: str, *, auto_migrate: bool = True) -> None:
        if not dsn.strip():
            raise ValueError("A PostgreSQL DSN is required for OIDC sessions.")
        self.dsn = dsn
        if auto_migrate:
            self.migrate()

    @staticmethod
    def _psycopg():
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:
            raise RuntimeError("OIDC sessions require psycopg.") from error
        return psycopg, dict_row

    def _connect(self):
        psycopg, dict_row = self._psycopg()
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def health_check(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 AS value").fetchone()["value"] == 1

    def migrate(self) -> None:
        migration = Path(__file__).with_name("migrations") / "0001_auth_sessions.sql"
        with self._connect() as connection:
            connection.execute(migration.read_text(encoding="utf-8"))

    @staticmethod
    def _transaction(row) -> LoginTransaction:
        return LoginTransaction(
            state_hash=row["state_hash"],
            nonce=row["nonce"],
            code_verifier=row["code_verifier"],
            return_to=row["return_to"],
            expires_at=row["expires_at"],
        )

    @staticmethod
    def _session(row) -> AuthSession:
        return AuthSession(
            session_hash=row["session_hash"],
            csrf_hash=row["csrf_hash"],
            principal=Principal(
                tenant_id=row["tenant_id"],
                user_id=row["user_id"],
                subject=row["subject"],
            ),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def create_transaction(self, value: LoginTransaction) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ava_oidc_transactions
                   (state_hash, nonce, code_verifier, return_to, expires_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (
                    value.state_hash,
                    value.nonce,
                    value.code_verifier,
                    value.return_to,
                    value.expires_at,
                ),
            )

    def consume_transaction(
        self, state_hash: str, now: datetime
    ) -> LoginTransaction | None:
        with self._connect() as connection:
            row = connection.execute(
                """DELETE FROM ava_oidc_transactions
                   WHERE state_hash=%s
                   RETURNING state_hash, nonce, code_verifier, return_to, expires_at""",
                (state_hash,),
            ).fetchone()
        if row is None or row["expires_at"] <= now:
            return None
        return self._transaction(row)

    def create_session(self, value: AuthSession) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ava_tenants (tenant_id) VALUES (%s)
                   ON CONFLICT DO NOTHING""",
                (value.principal.tenant_id,),
            )
            connection.execute(
                """INSERT INTO ava_users (tenant_id, user_id) VALUES (%s, %s)
                   ON CONFLICT DO NOTHING""",
                (value.principal.tenant_id, value.principal.user_id),
            )
            connection.execute(
                """INSERT INTO ava_auth_sessions
                   (session_hash, csrf_hash, tenant_id, user_id, subject, created_at, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    value.session_hash,
                    value.csrf_hash,
                    value.principal.tenant_id,
                    value.principal.user_id,
                    value.principal.subject,
                    value.created_at,
                    value.expires_at,
                ),
            )

    def get_session(self, session_hash: str, now: datetime) -> AuthSession | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM ava_auth_sessions
                   WHERE session_hash=%s AND expires_at>%s""",
                (session_hash, now),
            ).fetchone()
        return None if row is None else self._session(row)

    def delete_session(self, session_hash: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "DELETE FROM ava_auth_sessions WHERE session_hash=%s RETURNING session_hash",
                (session_hash,),
            ).fetchone()
        return row is not None

    def purge_expired(self, now: datetime) -> tuple[int, int]:
        with self._connect() as connection:
            transactions = connection.execute(
                "DELETE FROM ava_oidc_transactions WHERE expires_at<=%s RETURNING state_hash",
                (now,),
            ).fetchall()
            sessions = connection.execute(
                "DELETE FROM ava_auth_sessions WHERE expires_at<=%s RETURNING session_hash",
                (now,),
            ).fetchall()
        return len(transactions), len(sessions)
