"""
Gmail OAuth token encryption (security audit finding A1).

brand_gmail_service.py previously wrote the full OAuth token bundle - access
token, refresh token, and the shared app client_secret - as plaintext JSON to
brands.gmail_token. Shopify tokens already went through AES-256-GCM
(shopify_service.py's encrypt_token/decrypt_token); Gmail tokens now reuse
the exact same generic, already-tested utility. These tests cover only the
plumbing specific to brand_gmail_service.py (encrypt at write, decrypt at
read, legacy-plaintext-still-readable) - the crypto primitives themselves are
already covered by test_shopify_token_encryption.py.

All Google API / Supabase calls are mocked - no live services required.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("ENCRYPTION_SECRET", "test-encryption-secret-do-not-use-in-prod")
os.environ.setdefault("GMAIL_CLIENT_ID", "test-client-id")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "test-client-secret")

import asyncio  # noqa: E402
from src.services.brand_gmail_service import BrandGmailService  # noqa: E402
from src.services.shopify_service import encrypt_token, _AES256_PREFIX  # noqa: E402


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


_TOKEN_DATA = {
    "token": "ya29.fake-access-token",
    "refresh_token": "1//fake-refresh-token",
    "token_uri": "https://oauth2.googleapis.com/token",
    "client_id": "test-client-id",
    "client_secret": "test-client-secret",
    "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
    "expiry": None,
}


def test_handle_callback_stores_encrypted_token_not_plaintext():
    service = BrandGmailService()
    update_calls = []

    fake_creds = MagicMock(
        token="ya29.fake-access-token", refresh_token="1//fake-refresh-token",
        token_uri="https://oauth2.googleapis.com/token", client_id="test-client-id",
        client_secret="test-client-secret", scopes=["https://www.googleapis.com/auth/gmail.modify"],
        expiry=None,
    )
    fake_flow = MagicMock(credentials=fake_creds)
    fake_gmail_svc = MagicMock()
    fake_gmail_svc.users.return_value.getProfile.return_value.execute.return_value = {"emailAddress": "merchant@example.com"}

    def fake_update(table, match, data):
        update_calls.append((table, match, data))
        return [data]

    with patch("src.services.brand_gmail_service._verify_state", return_value="brand-1"), \
         patch("src.services.brand_gmail_service.Flow") as mock_flow_cls, \
         patch("src.services.brand_gmail_service.build", return_value=fake_gmail_svc), \
         patch("src.services.brand_gmail_service.supabase_update", side_effect=fake_update):
        mock_flow_cls.from_client_config.return_value = fake_flow
        result = run(service.handle_callback(state="signed-state", code="auth-code"))

    assert result["success"] is True
    brand_updates = [d for t, m, d in update_calls if t == "brands"]
    assert len(brand_updates) == 1
    stored_token = brand_updates[0]["gmail_token"]
    # Never plaintext JSON on the wire to the database.
    assert stored_token.startswith(_AES256_PREFIX)
    assert "fake-refresh-token" not in stored_token
    assert "test-client-secret" not in stored_token


def test_build_service_decrypts_encrypted_token():
    service = BrandGmailService()
    encrypted = encrypt_token(json.dumps(_TOKEN_DATA))
    brand = {"id": "brand-1", "gmail_token": encrypted, "gmail_email": "merchant@example.com"}

    fake_creds = MagicMock(refresh_token=None)  # skip the refresh branch entirely
    with patch("src.services.brand_gmail_service.Credentials", return_value=fake_creds) as mock_creds_cls:
        result = service._build_service(brand)

    assert result is not None
    # Confirms the encrypted blob was actually decrypted and parsed correctly
    # before being handed to Credentials(), not passed through raw.
    _, kwargs = mock_creds_cls.call_args
    assert kwargs["token"] == "ya29.fake-access-token"
    assert kwargs["refresh_token"] == "1//fake-refresh-token"


def test_build_service_still_reads_legacy_plaintext_token():
    """Backward compatibility: rows written before this fix (raw JSON, no
    encryption) must keep working - decrypt_token() no-ops on non-ciphertext,
    and get re-encrypted the next time the token is refreshed/rewritten."""
    service = BrandGmailService()
    legacy_plaintext = json.dumps(_TOKEN_DATA)
    brand = {"id": "brand-1", "gmail_token": legacy_plaintext, "gmail_email": "merchant@example.com"}

    fake_creds = MagicMock(refresh_token=None)
    with patch("src.services.brand_gmail_service.Credentials", return_value=fake_creds) as mock_creds_cls:
        result = service._build_service(brand)

    assert result is not None
    _, kwargs = mock_creds_cls.call_args
    assert kwargs["token"] == "ya29.fake-access-token"


def test_refresh_rewrites_token_encrypted():
    """When a stored token is refreshed, the rewritten value must also be
    encrypted - not silently dropped back to plaintext on the write path
    that update_calls hits after a successful refresh."""
    service = BrandGmailService()
    encrypted = encrypt_token(json.dumps(_TOKEN_DATA))
    brand = {"id": "brand-1", "gmail_token": encrypted, "gmail_email": "merchant@example.com"}

    fake_creds = MagicMock(refresh_token="1//fake-refresh-token", token="ya29.refreshed-token", expiry=None)
    update_calls = []

    def fake_update(table, match, data):
        update_calls.append((table, match, data))
        return [data]

    with patch("src.services.brand_gmail_service.Credentials", return_value=fake_creds), \
         patch("src.services.brand_gmail_service.Request"), \
         patch("src.services.brand_gmail_service.supabase_update", side_effect=fake_update):
        service._build_service(brand)

    token_updates = [d["gmail_token"] for t, m, d in update_calls if "gmail_token" in d]
    assert len(token_updates) == 1
    assert token_updates[0].startswith(_AES256_PREFIX)
