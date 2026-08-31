"""OIDC authorization-code/PKCE orchestration and opaque browser sessions."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Any, Callable
from urllib.parse import urlencode

import httpx

from .models import AuthSession, AuthenticatedSession, LoginTransaction
from .oidc import AuthenticationError, OIDCSettings, OIDCTokenVerifier
from .repository import AuthRepository


@dataclass(frozen=True)
class SessionSettings:
    cookie_name: str = "ava_session"
    csrf_cookie_name: str = "ava_csrf"
    login_ttl_seconds: int = 300
    session_ttl_seconds: int = 28_800
    cookie_secure: bool = True
    cookie_same_site: str = "lax"

    def validate(self) -> None:
        if self.login_ttl_seconds <= 0 or self.session_ttl_seconds <= 0:
            raise ValueError("Authentication TTL values must be positive.")
        if self.cookie_same_site not in {"lax", "strict"}:
            raise ValueError("Authentication cookies must use lax or strict SameSite.")
        if not self.cookie_name or not self.csrf_cookie_name:
            raise ValueError("Authentication cookie names cannot be empty.")


TokenExchange = Callable[[str, LoginTransaction], dict[str, Any]]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _random_token(bytes_count: int = 32) -> str:
    return secrets.token_urlsafe(bytes_count)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _safe_return_to(value: str) -> str:
    if not value.startswith("/") or value.startswith("//") or "\x00" in value:
        raise ValueError("return_to must be a local absolute path.")
    return value


class OIDCSessionService:
    def __init__(
        self,
        repository: AuthRepository,
        verifier: OIDCTokenVerifier,
        *,
        session_settings: SessionSettings | None = None,
        token_exchange: TokenExchange | None = None,
    ) -> None:
        self.repository = repository
        self.verifier = verifier
        self.oidc = verifier.settings
        self.settings = session_settings or SessionSettings()
        self.settings.validate()
        self._token_exchange = token_exchange or self._exchange_code

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)

    def begin_login(self, *, return_to: str = "/") -> str:
        return_to = _safe_return_to(return_to)
        state = _random_token()
        nonce = _random_token()
        verifier = _random_token(48)
        self.repository.create_transaction(
            LoginTransaction(
                state_hash=_hash(state),
                nonce=nonce,
                code_verifier=verifier,
                return_to=return_to,
                expires_at=self.now()
                + timedelta(seconds=self.settings.login_ttl_seconds),
            )
        )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.oidc.client_id,
                "redirect_uri": self.oidc.redirect_uri,
                "scope": " ".join(self.oidc.scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": _pkce_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        return self.verifier.metadata["authorization_endpoint"] + "?" + query

    def _exchange_code(
        self, code: str, transaction: LoginTransaction
    ) -> dict[str, Any]:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.oidc.redirect_uri,
            "client_id": self.oidc.client_id,
            "code_verifier": transaction.code_verifier,
        }
        auth = (
            httpx.BasicAuth(self.oidc.client_id, self.oidc.client_secret)
            if self.oidc.client_secret
            else None
        )
        try:
            response = httpx.post(
                self.verifier.metadata["token_endpoint"],
                data=data,
                auth=auth,
                headers={"Accept": "application/json"},
                timeout=self.oidc.discovery_timeout_seconds,
                follow_redirects=False,
            )
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise AuthenticationError(
                "The identity provider could not complete sign-in."
            ) from error
        if not isinstance(value, dict):
            raise AuthenticationError("The identity provider response is invalid.")
        return value

    def complete_login(self, *, state: str, code: str) -> AuthenticatedSession:
        if not state or not code:
            raise AuthenticationError("The identity callback is incomplete.")
        transaction = self.repository.consume_transaction(_hash(state), self.now())
        if transaction is None:
            raise AuthenticationError("The sign-in request expired or was already used.")
        tokens = self._token_exchange(code, transaction)
        id_token = tokens.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise AuthenticationError("The identity provider omitted the ID token.")
        principal = self.verifier.verify_id_token(id_token, nonce=transaction.nonce)
        raw_session = _random_token(48)
        raw_csrf = _random_token()
        now = self.now()
        session = AuthSession(
            session_hash=_hash(raw_session),
            csrf_hash=_hash(raw_csrf),
            principal=principal,
            created_at=now,
            expires_at=now + timedelta(seconds=self.settings.session_ttl_seconds),
        )
        self.repository.create_session(session)
        return AuthenticatedSession(raw_session, raw_csrf, session)

    def authenticate(self, token: str | None) -> AuthSession:
        if not token:
            raise AuthenticationError("Sign in to continue.")
        session = self.repository.get_session(_hash(token), self.now())
        if session is None:
            raise AuthenticationError("The session expired. Sign in again.")
        return session

    def require_csrf(self, session: AuthSession, supplied: str | None) -> None:
        if not supplied or not secrets.compare_digest(session.csrf_hash, _hash(supplied)):
            raise AuthenticationError("The request security token is invalid.")

    def logout(self, token: str | None) -> None:
        if token:
            self.repository.delete_session(_hash(token))
