"""
Shopify Access Token Encryption Tests (H1)
=============================================
Covers the AES-256-GCM upgrade in shopify_service.py (encrypt_token/
decrypt_token), backward compatibility with tokens already encrypted under
the old Fernet scheme (confirmed present in production — the "HafsaClothing"
brand), and the two write sites in v2_brands.py's connect_shopify() that
previously stored the access token in plaintext.

All Shopify/Supabase calls are mocked — no live services required.
"""
import base64
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("ENCRYPTION_SECRET", "test-encryption-secret-do-not-use-in-prod")

from src.services.shopify_service import encrypt_token, decrypt_token, _get_fernet_key, _AES256_PREFIX  # noqa: E402


# ─── 1. AES-256-GCM round trip ───────────────────────────────────────────────

def test_encrypt_decrypt_round_trip():
    token = "shpat_realistic_looking_token_1234567890abcdef"
    encrypted = encrypt_token(token)
    assert encrypted != token
    assert encrypted.startswith(_AES256_PREFIX)
    assert decrypt_token(encrypted) == token


def test_empty_value_encrypts_and_decrypts_to_empty_string():
    assert encrypt_token("") == ""
    assert decrypt_token("") == ""
    assert decrypt_token(None) == ""


def test_same_plaintext_encrypted_twice_produces_different_ciphertext():
    """Proves the nonce is fresh per call, not reused — reusing a GCM nonce
    with the same key is a real cryptographic break, not just a style nit."""
    token = "shpat_same_token"
    a = encrypt_token(token)
    b = encrypt_token(token)
    assert a != b
    assert decrypt_token(a) == token
    assert decrypt_token(b) == token


def test_ciphertext_never_contains_the_plaintext_token():
    token = "shpat_super_secret_value_xyz"
    encrypted = encrypt_token(token)
    assert token not in encrypted


# ─── 2. Backward compatibility with data already in production ──────────────

def test_decrypt_still_reads_legacy_fernet_encrypted_tokens():
    """The 'HafsaClothing' brand in production has a real Fernet-encrypted
    token predating this change. decrypt_token must keep reading it without
    requiring a data migration."""
    token = "shpat_legacy_fernet_token"
    legacy_ciphertext = Fernet(_get_fernet_key()).encrypt(token.encode()).decode()
    assert not legacy_ciphertext.startswith(_AES256_PREFIX)

    assert decrypt_token(legacy_ciphertext) == token


def test_decrypt_returns_legacy_plaintext_as_is():
    """Rows saved before encryption existed at all (or the v2_brands.py bug
    this PR fixes) must still round-trip through decrypt_token unchanged."""
    plaintext = "shpat_never_encrypted_token"
    assert decrypt_token(plaintext) == plaintext


def test_decrypt_handles_corrupted_aes_payload_gracefully():
    """A tampered/corrupted AES-256 payload must not crash the caller."""
    corrupted = _AES256_PREFIX + base64.urlsafe_b64encode(b"not-valid-ciphertext-data-1234").decode()
    result = decrypt_token(corrupted)
    assert isinstance(result, str)  # doesn't raise


# ─── 3. brand_manager.py's aliases use the same, now-upgraded functions ─────

def test_brand_manager_encrypt_decrypt_aliases_use_aes256():
    from src.services.brand_manager import _encrypt, _decrypt
    token = "shpat_via_brand_manager"
    encrypted = _encrypt(token)
    assert encrypted.startswith(_AES256_PREFIX)
    assert _decrypt(encrypted) == token


# ─── 4. v2_brands.py connect_shopify no longer writes plaintext ─────────────

app = FastAPI()
from src.api.routes.v2_brands import router as v2_brands_router  # noqa: E402
app.include_router(v2_brands_router, prefix="/api/v2")
client = TestClient(app)


@pytest.mark.asyncio
async def test_connect_shopify_stores_encrypted_token_not_plaintext():
    from src.services.auth_service import auth_service

    tenant_id = "11111111-1111-1111-1111-111111111111"
    brand_id = "22222222-2222-2222-2222-222222222222"
    submitted_token = "shpat_plaintext_from_the_client_request"
    brand_row = {"id": brand_id, "tenant_id": tenant_id, "name": "Test Brand", "is_active": True}
    captured_updates = {}

    def fake_select(table, params=None):
        params = params or {}
        if table == "brands" and params.get("id", "").replace("eq.", "") == brand_id:
            return [brand_row]
        return []

    def fake_update(table, match, data):
        captured_updates.update(data)
        brand_row.update(data)
        return [brand_row]

    token = auth_service.create_access_token(tenant_id, "owner@example.com")

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_update", side_effect=fake_update), \
         patch("src.services.shopify_service.ShopifyClient.validate_connection",
               new=AsyncMock(return_value={"success": True, "shop_name": "Test Shop", "shop_domain": "test-shop.myshopify.com"})):
        resp = client.post(
            f"/api/v2/brands/{brand_id}/shopify/connect",
            json={"shop_domain": "test-shop", "access_token": submitted_token},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    stored_token = captured_updates["shopify_access_token"]
    assert stored_token != submitted_token  # not plaintext
    assert stored_token.startswith(_AES256_PREFIX)
    assert decrypt_token(stored_token) == submitted_token  # and it's the *correct* ciphertext
