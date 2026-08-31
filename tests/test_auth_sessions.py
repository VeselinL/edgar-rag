import base64
from datetime import timedelta
import hashlib
import time
import unittest
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from src.auth.oidc import AuthenticationError, OIDCSettings, OIDCTokenVerifier
from src.auth.repository import InMemoryAuthRepository
from src.auth.service import OIDCSessionService, SessionSettings


class AuthSessionTests(unittest.TestCase):
    def setUp(self):
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = self.private_key.public_key()
        settings = OIDCSettings(
            issuer="https://identity.example.test",
            client_id="ava-web",
            client_secret="server-secret",
            redirect_uri="https://ava.example.test/api/auth/callback",
            fixed_tenant_id="tenant-a",
            clock_skew_seconds=0,
        )
        self.verifier = OIDCTokenVerifier(
            settings,
            metadata_loader=lambda: {
                "issuer": settings.issuer,
                "authorization_endpoint": settings.issuer + "/authorize",
                "token_endpoint": settings.issuer + "/token",
                "jwks_uri": settings.issuer + "/jwks",
            },
            signing_key_loader=lambda _token, _algorithm: public_key,
        )
        self.repository = InMemoryAuthRepository()
        self.exchanges = []

        def exchange(code, transaction):
            self.exchanges.append((code, transaction))
            now = int(time.time())
            return {
                "id_token": jwt.encode(
                    {
                        "iss": settings.issuer,
                        "aud": settings.client_id,
                        "sub": "user-a",
                        "iat": now,
                        "exp": now + 300,
                        "nonce": transaction.nonce,
                    },
                    self.private_key,
                    algorithm="RS256",
                )
            }

        self.service = OIDCSessionService(
            self.repository,
            self.verifier,
            session_settings=SessionSettings(
                login_ttl_seconds=300,
                session_ttl_seconds=3600,
            ),
            token_exchange=exchange,
        )

    def test_login_uses_code_flow_pkce_nonce_and_local_return_path(self):
        url = self.service.begin_login(return_to="/research?company=TSLA")
        query = parse_qs(urlparse(url).query)

        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["scope"], ["openid profile"])
        transaction = next(iter(self.repository.transactions.values()))
        expected_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(transaction.code_verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        self.assertEqual(query["code_challenge"], [expected_challenge])
        self.assertEqual(transaction.return_to, "/research?company=TSLA")
        self.assertNotIn(query["state"][0], self.repository.transactions)

        for unsafe in ("https://attacker.example/", "//attacker.example", "relative"):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    self.service.begin_login(return_to=unsafe)

    def test_callback_is_one_time_and_creates_hashed_session_and_csrf(self):
        login_url = self.service.begin_login()
        state = parse_qs(urlparse(login_url).query)["state"][0]

        authenticated = self.service.complete_login(state=state, code="one-time-code")

        self.assertEqual(authenticated.session.principal.user_id, "user-a")
        self.assertNotIn(authenticated.token, self.repository.sessions)
        loaded = self.service.authenticate(authenticated.token)
        self.assertEqual(loaded.principal.tenant_id, "tenant-a")
        self.service.require_csrf(loaded, authenticated.csrf_token)
        with self.assertRaises(AuthenticationError):
            self.service.require_csrf(loaded, "wrong-token")
        with self.assertRaises(AuthenticationError):
            self.service.complete_login(state=state, code="replayed-code")

    def test_logout_and_expiry_invalidate_session(self):
        login_url = self.service.begin_login()
        state = parse_qs(urlparse(login_url).query)["state"][0]
        authenticated = self.service.complete_login(state=state, code="code")
        self.service.logout(authenticated.token)
        with self.assertRaises(AuthenticationError):
            self.service.authenticate(authenticated.token)

        expired = authenticated.session
        self.repository.sessions[expired.session_hash] = type(expired)(
            **{**expired.__dict__, "expires_at": self.service.now() - timedelta(seconds=1)}
        )
        with self.assertRaises(AuthenticationError):
            self.service.authenticate(authenticated.token)

    def test_expired_login_transaction_is_consumed_and_rejected(self):
        login_url = self.service.begin_login()
        state = parse_qs(urlparse(login_url).query)["state"][0]
        state_hash, transaction = next(iter(self.repository.transactions.items()))
        self.repository.transactions[state_hash] = type(transaction)(
            **{
                **transaction.__dict__,
                "expires_at": self.service.now() - timedelta(seconds=1),
            }
        )

        with self.assertRaises(AuthenticationError):
            self.service.complete_login(state=state, code="code")
        self.assertEqual(self.exchanges, [])


if __name__ == "__main__":
    unittest.main()
