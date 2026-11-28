"""Encryption tests for the currently supported Gmail send-only OAuth token."""
import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("ENCRYPTION_SECRET", "test-encryption-secret-do-not-use-in-prod")
os.environ.setdefault("GMAIL_SEND_CLIENT_ID", "test-send-client-id")
os.environ.setdefault("GMAIL_SEND_CLIENT_SECRET", "test-send-client-secret")

import asyncio  # noqa: E402
from src.services.brand_gmail_service import BrandGmailService, _sign_state  # noqa: E402
from src.services.shopify_service import encrypt_token, _AES256_PREFIX  # noqa: E402

SEND_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "openid",
    "email",
]

_TOKEN_DATA = {
    "token": "ya29.fake-access-token",
    "refresh_token": "1//fake-refresh-token",
    "token_uri": "https://oauth2.googleapis.com/token",
    "client_id": "test-send-client-id",
    "client_secret": "test-send-client-secret",
    "scopes": SEND_SCOPES,
    "scope_mode": "send_only",
    "expiry": None,
}


def run(coro):
    return asyncio.run(coro)


def _send_brand(token):
    return {
        "id": "brand-1",
        "email_ingestion_mode": "forwarding",
        "gmail_send_connected": True,
        "gmail_send_token": token,
        "gmail_send_email": "merchant@example.com",
    }


def test_handle_callback_stores_encrypted_send_only_token():
    service = BrandGmailService()
    update_calls = []
    fake_creds = MagicMock(
        token="ya29.fake-access-token",
        refresh_token="1//fake-refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="test-send-client-id",
        client_secret="test-send-client-secret",
        scopes=SEND_SCOPES,
        granted_scopes=SEND_SCOPES,
        id_token="fake-id-token",
        expiry=None,
    )
    fake_flow = MagicMock(credentials=fake_creds)

    def fake_update(table, match, data):
        update_calls.append((table, match, data))
        return [data]

    with patch("src.services.brand_gmail_service.Flow") as mock_flow_cls, \
         patch("src.services.brand_gmail_service._get_client_config", return_value={"web": {}}), \
         patch("google.oauth2.id_token.verify_oauth2_token", return_value={
             "email": "merchant@example.com", "email_verified": True
         }), \
         patch("src.services.brand_gmail_service.supabase_update", side_effect=fake_update):
        mock_flow_cls.from_client_config.return_value = fake_flow
        result = run(service.handle_callback(
            state=_sign_state("brand-1", scope_mode="send_only"),
            code="auth-code",
        ))

    assert result["success"] is True
    brand_updates = [data for table, _, data in update_calls if table == "brands"]
    assert len(brand_updates) == 1
    stored = brand_updates[0]["gmail_send_token"]
    assert stored.startswith(_AES256_PREFIX)
    assert "fake-refresh-token" not in stored
    assert "test-send-client-secret" not in stored
    assert brand_updates[0]["gmail_send_connected"] is True
    assert "gmail_token" not in brand_updates[0]


def test_build_service_decrypts_encrypted_send_only_token():
    service = BrandGmailService()
    encrypted = encrypt_token(json.dumps(_TOKEN_DATA))
    brand = _send_brand(encrypted)
    fake_creds = MagicMock(refresh_token=None)

    with patch("src.services.brand_gmail_service.Credentials", return_value=fake_creds) as mock_creds_cls, \
         patch("src.services.brand_gmail_service.build", return_value="gmail-service"):
        result = service._build_service(brand)

    assert result == "gmail-service"
    _, kwargs = mock_creds_cls.call_args
    assert kwargs["token"] == "ya29.fake-access-token"
    assert kwargs["refresh_token"] == "1//fake-refresh-token"
    assert kwargs["scopes"] == SEND_SCOPES


def test_legacy_inbox_token_is_never_used_to_build_a_service():
    service = BrandGmailService()
    legacy = json.dumps({
        **_TOKEN_DATA,
        "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
        "scope_mode": "full",
    })
    brand = {
        "id": "brand-1",
        "email_ingestion_mode": "gmail_polling",
        "gmail_connected": True,
        "gmail_token": legacy,
        "gmail_email": "merchant@example.com",
    }

    with patch("src.services.brand_gmail_service.Credentials") as credentials, \
         patch("src.services.brand_gmail_service.build") as build:
        result = service._build_service(brand)

    assert result is None
    credentials.assert_not_called()
    build.assert_not_called()


def test_refresh_rewrites_send_only_token_encrypted():
    service = BrandGmailService()
    encrypted = encrypt_token(json.dumps(_TOKEN_DATA))
    brand = _send_brand(encrypted)
    fake_creds = MagicMock(
        refresh_token="1//fake-refresh-token",
        token="ya29.refreshed-token",
        expiry=None,
    )
    update_calls = []

    def fake_update(table, match, data):
        update_calls.append((table, match, data))
        return [data]

    with patch("src.services.brand_gmail_service.Credentials", return_value=fake_creds), \
         patch("src.services.brand_gmail_service.Request"), \
         patch("src.services.brand_gmail_service.supabase_update", side_effect=fake_update), \
         patch("src.services.brand_gmail_service.build", return_value="gmail-service"):
        service._build_service(brand)

    token_updates = [
        data["gmail_send_token"]
        for table, _, data in update_calls
        if "gmail_send_token" in data
    ]
    assert len(token_updates) == 1
    assert token_updates[0].startswith(_AES256_PREFIX)
    assert "gmail_token" not in update_calls[0][2]
