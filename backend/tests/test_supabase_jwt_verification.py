"""
Supabase JWT Verification Tests (ES256 regression)
=====================================================
Real crypto round-trip, not a mocked one — generates an actual EC keypair,
signs a token with ES256 exactly like Supabase's current asymmetric-key
projects do, and confirms verify_jwt() can decode it via the JWKS path.

Regression coverage for a production incident: verify_jwt() hardcoded
`algorithms=["RS256"]` for the JWKS path. Supabase projects created since
the asymmetric-key rollout sign with ES256, so PyJWT rejected every real
access token with InvalidAlgorithmError (a subclass of InvalidTokenError,
caught silently) — every protected endpoint 401'd right after a successful
login, since login itself doesn't call verify_jwt but everything after it
does.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.supabase_auth_service import SupabaseAuthService  # noqa: E402


def _es256_token(payload: dict):
    """Sign a token with a fresh EC (P-256) key, exactly as Supabase's
    current projects do — returns (token, public_key) for verification."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(payload, private_key, algorithm="ES256")
    return token, private_key.public_key()


def _service_with_fake_jwks(public_key):
    service = SupabaseAuthService()
    fake_signing_key = MagicMock()
    fake_signing_key.key = public_key
    fake_jwks_client = MagicMock()
    fake_jwks_client.get_signing_key_from_jwt.return_value = fake_signing_key
    service._jwks_client = fake_jwks_client
    return service


def test_verify_jwt_accepts_a_real_es256_supabase_token():
    payload = {
        "sub": "auth-user-1",
        "email": "user@example.com",
        "aud": "authenticated",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "iat": datetime.now(timezone.utc),
    }
    token, public_key = _es256_token(payload)
    service = _service_with_fake_jwks(public_key)

    decoded = service.verify_jwt(token)

    assert decoded is not None
    assert decoded["sub"] == "auth-user-1"
    assert decoded["email"] == "user@example.com"


def test_verify_jwt_rejects_an_expired_es256_token():
    payload = {
        "sub": "auth-user-1",
        "aud": "authenticated",
        "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        "iat": datetime.now(timezone.utc) - timedelta(hours=2),
    }
    token, public_key = _es256_token(payload)
    service = _service_with_fake_jwks(public_key)

    assert service.verify_jwt(token) is None


def test_verify_jwt_rejects_a_token_signed_by_the_wrong_key():
    payload = {
        "sub": "auth-user-1",
        "aud": "authenticated",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "iat": datetime.now(timezone.utc),
    }
    token, _real_public_key = _es256_token(payload)
    _, wrong_public_key = _es256_token({"sub": "someone-else"})
    service = _service_with_fake_jwks(wrong_public_key)

    assert service.verify_jwt(token) is None
