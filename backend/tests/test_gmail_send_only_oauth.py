import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("GMAIL_CLIENT_ID", "client-id")

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
    _, kwargs = mock_flow.from_client_config.call_args
    assert kwargs["scopes"] == SEND_ONLY_SCOPES

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
