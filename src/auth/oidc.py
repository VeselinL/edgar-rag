"""Strict provider-neutral OpenID Connect ID-token verification."""

from __future__ import annotations

from dataclasses import dataclass
import os
import secrets
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

import httpx
import jwt


class AuthenticationError(RuntimeError):
    """A browser-safe authentication failure without provider details."""


@dataclass(frozen=True)
class Principal:
    """Only verified server-side identity values used for data ownership."""

    tenant_id: str
    user_id: str
    subject: str


@dataclass(frozen=True)
class OIDCSettings:
    issuer: str
    client_id: str
    redirect_uri: str
    client_secret: str | None = None
    fixed_tenant_id: str | None = None
    tenant_claim: str | None = None
    algorithms: tuple[str, ...] = ("RS256",)
    scopes: tuple[str, ...] = ("openid", "profile")
    required_claims: tuple[str, ...] = ("exp", "iat", "iss", "aud", "sub")
    discovery_timeout_seconds: float = 5.0
    clock_skew_seconds: int = 30
    allow_insecure_http: bool = False

    @classmethod
    def from_environment(cls) -> "OIDCSettings":
        algorithms = tuple(
            value.strip()
            for value in os.getenv("AVA_OIDC_ALGORITHMS", "RS256").split(",")
            if value.strip()
        )
        scopes = tuple(
            value.strip()
            for value in os.getenv("AVA_OIDC_SCOPES", "openid profile").split()
            if value.strip()
        )
        settings = cls(
            issuer=os.getenv("AVA_OIDC_ISSUER", "").strip().rstrip("/"),
            client_id=os.getenv("AVA_OIDC_CLIENT_ID", "").strip(),
            client_secret=os.getenv("AVA_OIDC_CLIENT_SECRET") or None,
            redirect_uri=os.getenv("AVA_OIDC_REDIRECT_URI", "").strip(),
            fixed_tenant_id=os.getenv("AVA_OIDC_TENANT_ID") or None,
            tenant_claim=os.getenv("AVA_OIDC_TENANT_CLAIM") or None,
            algorithms=algorithms,
            scopes=scopes,
            discovery_timeout_seconds=float(
                os.getenv("AVA_OIDC_DISCOVERY_TIMEOUT_SECONDS", "5")
            ),
            clock_skew_seconds=int(os.getenv("AVA_OIDC_CLOCK_SKEW_SECONDS", "30")),
            allow_insecure_http=(
                os.getenv("AVA_OIDC_ALLOW_INSECURE_HTTP", "false")
                .strip()
                .casefold()
                == "true"
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.issuer or not self.client_id or not self.redirect_uri:
            raise ValueError(
                "OIDC requires AVA_OIDC_ISSUER, AVA_OIDC_CLIENT_ID, and "
                "AVA_OIDC_REDIRECT_URI."
            )
        issuer = urlparse(self.issuer)
        redirect = urlparse(self.redirect_uri)
        loopback = issuer.hostname in {"localhost", "127.0.0.1", "::1"}
        if issuer.scheme != "https" and not (self.allow_insecure_http and loopback):
            raise ValueError("AVA_OIDC_ISSUER must use HTTPS outside loopback development.")
        if redirect.scheme not in {"https", "http"} or not redirect.netloc:
            raise ValueError("AVA_OIDC_REDIRECT_URI must be an absolute HTTP(S) URL.")
        if bool(self.fixed_tenant_id) == bool(self.tenant_claim):
            raise ValueError(
                "Configure exactly one of AVA_OIDC_TENANT_ID or "
                "AVA_OIDC_TENANT_CLAIM."
            )
        if not self.algorithms or any(value.casefold() == "none" for value in self.algorithms):
            raise ValueError("AVA_OIDC_ALGORITHMS must contain approved signed algorithms.")
        if "openid" not in self.scopes:
            raise ValueError("AVA_OIDC_SCOPES must include openid.")
        if self.discovery_timeout_seconds <= 0 or self.clock_skew_seconds < 0:
            raise ValueError("OIDC timeout must be positive and clock skew non-negative.")


MetadataLoader = Callable[[], dict[str, Any]]
SigningKeyLoader = Callable[[str, str], Any]


class OIDCTokenVerifier:
    """Validate ID tokens using trusted discovery metadata and signing keys."""

    def __init__(
        self,
        settings: OIDCSettings,
        *,
        metadata_loader: MetadataLoader | None = None,
        signing_key_loader: SigningKeyLoader | None = None,
    ) -> None:
        settings.validate()
        self.settings = settings
        self._metadata_loader = metadata_loader or self._load_metadata
        self._signing_key_loader = signing_key_loader
        self._metadata: dict[str, Any] | None = None
        self._jwks_client: jwt.PyJWKClient | None = None

    @property
    def metadata(self) -> dict[str, Any]:
        if self._metadata is None:
            value = self._metadata_loader()
            if value.get("issuer") != self.settings.issuer:
                raise AuthenticationError("The identity provider configuration is invalid.")
            required = ("authorization_endpoint", "token_endpoint", "jwks_uri")
            if any(not isinstance(value.get(field), str) or not value[field] for field in required):
                raise AuthenticationError("The identity provider configuration is incomplete.")
            for field in required:
                parsed = urlparse(value[field])
                loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                if parsed.scheme != "https" and not (
                    self.settings.allow_insecure_http and loopback
                ):
                    raise AuthenticationError("The identity provider endpoint is insecure.")
            self._metadata = value
        return self._metadata

    def _load_metadata(self) -> dict[str, Any]:
        url = self.settings.issuer + "/.well-known/openid-configuration"
        try:
            response = httpx.get(
                url,
                timeout=self.settings.discovery_timeout_seconds,
                follow_redirects=False,
            )
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise AuthenticationError(
                "The identity provider is temporarily unavailable."
            ) from error
        if not isinstance(value, dict):
            raise AuthenticationError("The identity provider configuration is invalid.")
        return value

    def _signing_key(self, token: str, algorithm: str) -> Any:
        if self._signing_key_loader is not None:
            return self._signing_key_loader(token, algorithm)
        if self._jwks_client is None:
            self._jwks_client = jwt.PyJWKClient(
                self.metadata["jwks_uri"],
                cache_keys=True,
                lifespan=300,
                timeout=self.settings.discovery_timeout_seconds,
            )
        try:
            return self._jwks_client.get_signing_key_from_jwt(token).key
        except jwt.PyJWTError as error:
            raise AuthenticationError("The identity token signature is invalid.") from error

    @staticmethod
    def _non_empty_claim(claims: dict[str, Any], name: str) -> str:
        value = claims.get(name)
        if not isinstance(value, str) or not value.strip():
            raise AuthenticationError(f"The identity token is missing required claim {name}.")
        return value.strip()

    def verify_id_token(self, token: str, *, nonce: str) -> Principal:
        if not token or not nonce:
            raise AuthenticationError("The identity response is incomplete.")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as error:
            raise AuthenticationError("The identity token is malformed.") from error
        algorithm = header.get("alg")
        if not isinstance(algorithm, str) or algorithm not in self.settings.algorithms:
            raise AuthenticationError("The identity token algorithm is not allowed.")
        try:
            claims = jwt.decode(
                token,
                self._signing_key(token, algorithm),
                algorithms=list(self.settings.algorithms),
                audience=self.settings.client_id,
                issuer=self.settings.issuer,
                leeway=self.settings.clock_skew_seconds,
                options={"require": list(self.settings.required_claims)},
            )
        except jwt.PyJWTError as error:
            raise AuthenticationError("The identity token could not be verified.") from error
        token_nonce = self._non_empty_claim(claims, "nonce")
        if not secrets.compare_digest(token_nonce, nonce):
            raise AuthenticationError("The identity response nonce is invalid.")
        subject = self._non_empty_claim(claims, "sub")
        if self.settings.fixed_tenant_id:
            tenant_id = self.settings.fixed_tenant_id
        else:
            tenant_id = self._non_empty_claim(claims, self.settings.tenant_claim or "")
        return Principal(tenant_id=tenant_id, user_id=subject, subject=subject)


def has_required_scopes(claims: dict[str, Any], required: Sequence[str]) -> bool:
    """Normalize common OAuth scope claim shapes for future API authorization."""
    value = claims.get("scope", claims.get("scp", ""))
    present = set(value.split()) if isinstance(value, str) else set(value or [])
    return set(required).issubset(present)
