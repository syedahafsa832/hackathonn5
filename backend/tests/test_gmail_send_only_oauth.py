import os
import sys
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("GMAIL_CLIENT_ID", "legacy-client-id")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "legacy-client-secret")
os.environ.setdefault("GMAIL_SEND_CLIENT_ID", "send-only-client-id")
os.environ.setdefault("GMAIL_SEND_CLIENT_SECRET", "send-only-client-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.brand_gmail_service import (  # noqa: E402
    BrandGmailService,
    SEND_ONLY_SCOPES,
    _sign_state,
    _state_scope_mode,
    _verify_state,
)


def test_send_only_oauth_url_requests_gmail_send_not_mailbox_read():
    service = BrandGmailService()
    fake_flow = MagicMock()
    fake_flow.authorization_url.return_value = ("https://accounts.google.com/test", None)

    with patch("src.services.brand_gmail_service.Flow") as mock_flow:
        mock_flow.from_client_config.return_value = fake_flow
        url = service.get_send_auth_url("brand-1")

    assert url.startswith("https://accounts.google.com/")
    config, kwargs = mock_flow.from_client_config.call_args
    assert kwargs["scopes"] == SEND_ONLY_SCOPES
    assert config["web"]["client_id"] == "send-only-client-id"

    auth_kwargs = fake_flow.authorization_url.call_args.kwargs
    assert auth_kwargs["include_granted_scopes"] == "false"
    assert _verify_state(auth_kwargs["state"]) == "brand-1"
    assert _state_scope_mode(auth_kwargs["state"]) == "send_only"
    assert "https://www.googleapis.com/auth/gmail.modify" not in kwargs["scopes"]


def test_legacy_oauth_state_defaults_to_full_scope_mode():
    state = _sign_state("brand-legacy")

    assert _verify_state(state) == "brand-legacy"
    assert _state_scope_mode(state) == "full"


def test_forwarded_reply_message_contains_rfc_threading_headers():
    message = BrandGmailService._build_message(
        "Your order is on the way.",
        in_reply_to="<customer@example.com>",
        references=["<root@example.com>"],
    )

    assert message["In-Reply-To"] == "<customer@example.com>"
    assert message["References"] == "<root@example.com> <customer@example.com>"
    assert message["Message-ID"]


def test_send_only_callback_uses_identity_token_and_never_calls_gmail_profile():
    service = BrandGmailService()
    state = _sign_state("brand-2", scope_mode="send_only")
    fake_flow = MagicMock()
    fake_creds = MagicMock(
        token="access-token",
        refresh_token="refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="send-only-client-id",
        client_secret="send-only-client-secret",
        scopes=SEND_ONLY_SCOPES,
        granted_scopes=SEND_ONLY_SCOPES,
        expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        id_token="verified-id-token",
    )
    fake_flow.credentials = fake_creds
    fake_gmail_service = MagicMock()
    update_calls = []

    def fake_update(table, match, data):
        update_calls.append((table, match, data))
        return [data]

    with patch("src.services.brand_gmail_service.Flow") as mock_flow:
        with patch("google.oauth2.id_token.verify_oauth2_token", return_value={
            "email": "merchant@example.com", "email_verified": True,
        }) as mock_verify_id_token:
            with patch("src.services.brand_gmail_service.supabase_update", side_effect=fake_update), patch(
                "src.services.brand_gmail_service.encrypt_token", return_value="encrypted-token"
            ), patch("src.services.brand_gmail_service.build", return_value=fake_gmail_service) as mock_build:
                mock_flow.from_client_config.return_value = fake_flow
                result = asyncio.run(service.handle_callback(state=state, code="auth-code"))

    assert result == {"success": True, "email": "merchant@example.com"}
    config, kwargs = mock_flow.from_client_config.call_args
    assert config["web"]["client_id"] == "send-only-client-id"
    assert kwargs["scopes"] == SEND_ONLY_SCOPES
    fake_flow.fetch_token.assert_called_once_with(code="auth-code")
    mock_verify_id_token.assert_called_once()
    mock_build.assert_not_called()  # gmail.send cannot call users.getProfile
    assert len(update_calls) == 1
    persisted = update_calls[0][2]
    assert persisted["gmail_scope_mode"] == "send_only"
    assert persisted["gmail_connected"] is True
