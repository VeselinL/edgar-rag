"""Authentication transaction and session records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .oidc import Principal


@dataclass(frozen=True)
class LoginTransaction:
    state_hash: str
    nonce: str
    code_verifier: str
    return_to: str
    expires_at: datetime


@dataclass(frozen=True)
class AuthSession:
    session_hash: str
    csrf_hash: str
    principal: Principal
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class AuthenticatedSession:
    token: str
    csrf_token: str
    session: AuthSession
    return_to: str
