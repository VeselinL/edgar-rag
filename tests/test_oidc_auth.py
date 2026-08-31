import time
import unittest

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from src.auth.oidc import AuthenticationError, OIDCSettings, OIDCTokenVerifier


class OIDCAuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public_key = cls.private_key.public_key()

    def settings(self, **updates):
        values = {
            "issuer": "https://identity.example.test",
            "client_id": "ava-web",
            "redirect_uri": "https://ava.example.test/api/auth/callback",
            "fixed_tenant_id": "ava-production",
            "algorithms": ("RS256",),
            "clock_skew_seconds": 0,
        }
        values.update(updates)
        return OIDCSettings(**values)

    def token(self, **updates):
        now = int(time.time())
        claims = {
            "iss": "https://identity.example.test",
            "aud": "ava-web",
            "sub": "provider-user-123",
            "iat": now,
            "exp": now + 300,
            "nonce": "expected-nonce",
        }
        claims.update(updates)
        return jwt.encode(claims, self.private_key, algorithm="RS256")

    def verifier(self, settings=None):
        return OIDCTokenVerifier(
            settings or self.settings(),
            metadata_loader=lambda: {
                "issuer": "https://identity.example.test",
                "authorization_endpoint": "https://identity.example.test/authorize",
                "token_endpoint": "https://identity.example.test/token",
                "jwks_uri": "https://identity.example.test/jwks",
            },
            signing_key_loader=lambda _token, _algorithm: self.public_key,
        )

    def test_valid_token_maps_only_verified_owner_identity(self):
        principal = self.verifier().verify_id_token(
            self.token(), nonce="expected-nonce"
        )

        self.assertEqual(principal.tenant_id, "ava-production")
        self.assertEqual(principal.user_id, "provider-user-123")
        self.assertEqual(principal.subject, "provider-user-123")

    def test_dynamic_tenant_requires_verified_non_empty_claim(self):
        verifier = self.verifier(
            self.settings(fixed_tenant_id=None, tenant_claim="tenant_id")
        )
        principal = verifier.verify_id_token(
            self.token(tenant_id="tenant-a"), nonce="expected-nonce"
        )
        self.assertEqual(principal.tenant_id, "tenant-a")

        with self.assertRaises(AuthenticationError):
            verifier.verify_id_token(self.token(), nonce="expected-nonce")

    def test_rejects_wrong_nonce_issuer_audience_expiry_and_algorithm(self):
        invalid = (
            (self.token(), "wrong-nonce"),
            (self.token(iss="https://attacker.example.test"), "expected-nonce"),
            (self.token(aud="other-client"), "expected-nonce"),
            (self.token(exp=int(time.time()) - 1), "expected-nonce"),
            (
                jwt.encode(
                    {
                        "iss": "https://identity.example.test",
                        "aud": "ava-web",
                        "sub": "provider-user-123",
                        "iat": int(time.time()),
                        "exp": int(time.time()) + 300,
                        "nonce": "expected-nonce",
                    },
                    "a-secret-that-must-never-be-accepted",
                    algorithm="HS256",
                ),
                "expected-nonce",
            ),
        )
        for token, nonce in invalid:
            with self.subTest(nonce=nonce):
                with self.assertRaises(AuthenticationError):
                    self.verifier().verify_id_token(token, nonce=nonce)

    def test_settings_require_https_and_exactly_one_tenant_source(self):
        invalid = (
            self.settings(issuer="http://identity.example.test"),
            self.settings(fixed_tenant_id=None, tenant_claim=None),
            self.settings(tenant_claim="tenant_id"),
            self.settings(algorithms=("none",)),
            self.settings(scopes=("profile",)),
        )
        for settings in invalid:
            with self.subTest(settings=settings):
                with self.assertRaises(ValueError):
                    settings.validate()


if __name__ == "__main__":
    unittest.main()
