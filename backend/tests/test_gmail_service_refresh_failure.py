"""
_build_service must never return a Gmail service built from stale,
unrefreshed credentials after a non-invalid_grant refresh failure - that
guaranteed a second, later failure at the actual API call instead of
failing fast (see brand_gmail_service.py's _build_service).
"""
import os
import sys
import json
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.brand_gmail_service import BrandGmailService  # noqa: E402


def test_transient_refresh_failure_returns_none_not_a_stale_service():
    brand = {"id": "brand-1", "gmail_token": "enc", "name": "Test", "gmail_email": "b@x.com"}
    svc = BrandGmailService()

    with patch("src.services.brand_gmail_service.decrypt_token", return_value=json.dumps({
        "token": "old", "refresh_token": "rt", "client_id": "cid", "client_secret": "cs",
    })), \
         patch("src.services.brand_gmail_service.Credentials") as MockCreds, \
         patch("src.services.brand_gmail_service.build") as mock_build:
        mock_creds = MagicMock(refresh_token="rt")
        mock_creds.refresh.side_effect = Exception("temporary network error")
        MockCreds.return_value = mock_creds

        result = svc._build_service(brand)

    assert result is None
    mock_build.assert_not_called()



def test_unexpired_access_token_does_not_trigger_refresh():
    brand = {"id": "brand-1", "gmail_token": "enc", "name": "Test", "gmail_email": "b@x.com"}
    svc = BrandGmailService()
    fake_service = MagicMock()
    valid_expiry = datetime.now(timezone.utc) + timedelta(minutes=45)

    with patch("src.services.brand_gmail_service.decrypt_token", return_value=json.dumps({
        "token": "valid-access-token", "refresh_token": "rt",
        "client_id": "cid", "client_secret": "cs",
        "expiry": valid_expiry.isoformat(),
    })), \
         patch("src.services.brand_gmail_service.Credentials") as MockCreds, \
         patch("src.services.brand_gmail_service.Request") as mock_request, \
         patch("src.services.brand_gmail_service.supabase_update") as mock_update, \
         patch("src.services.brand_gmail_service.build", return_value=fake_service) as mock_build:
        mock_creds = MagicMock(
            token="valid-access-token",
            refresh_token="rt",
            expiry=valid_expiry,
            expired=False,
        )
        MockCreds.return_value = mock_creds

        result = svc._build_service(brand)

    assert result is fake_service
    mock_creds.refresh.assert_not_called()
    mock_request.assert_not_called()
    mock_update.assert_not_called()
    mock_build.assert_called_once_with("gmail", "v1", credentials=mock_creds)


def test_unknown_expiry_triggers_refresh_for_legacy_token():
    brand = {"id": "brand-1", "gmail_token": "enc", "name": "Test", "gmail_email": "b@x.com"}
    svc = BrandGmailService()
    update_calls = []
    fresh_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    def fake_update(table, match, data):
        update_calls.append((table, match, data))
        return [data]

    def fake_refresh(_request):
        mock_creds.token = "refreshed-access-token"
        mock_creds.expiry = fresh_expiry
        mock_creds.expired = False

    with patch("src.services.brand_gmail_service.decrypt_token", return_value=json.dumps({
        "token": "legacy-access-token", "refresh_token": "rt",
        "client_id": "cid", "client_secret": "cs",
        # No expiry: old stored rows need one refresh to establish it.
    })), \
         patch("src.services.brand_gmail_service.Credentials") as MockCreds, \
         patch("src.services.brand_gmail_service.Request"), \
         patch("src.services.brand_gmail_service.supabase_update", side_effect=fake_update), \
         patch("src.services.brand_gmail_service.build") as mock_build:
        mock_creds = MagicMock(
            token="legacy-access-token",
            refresh_token="rt",
            expiry=None,
            expired=False,
        )
        mock_creds.refresh.side_effect = fake_refresh
        MockCreds.return_value = mock_creds

        svc._build_service(brand)

    mock_creds.refresh.assert_called_once()
    mock_build.assert_called_once_with("gmail", "v1", credentials=mock_creds)
    token_updates = [data["gmail_token"] for table, match, data in update_calls if "gmail_token" in data]
    assert len(token_updates) == 1


def test_revoked_refresh_token_marks_disconnected_and_returns_no_service():
    brand = {"id": "brand-1", "gmail_token": "enc", "name": "Test", "gmail_email": "b@x.com"}
    svc = BrandGmailService()
    update_calls = []

    def fake_update(table, match, data):
        update_calls.append((table, match, data))
        return [data]

    with patch("src.services.brand_gmail_service.decrypt_token", return_value=json.dumps({
        "token": "expired-access-token", "refresh_token": "revoked-refresh-token",
        "client_id": "cid", "client_secret": "cs",
        "expiry": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
    })), \
         patch("src.services.brand_gmail_service.Credentials") as MockCreds, \
         patch("src.services.brand_gmail_service.Request"), \
         patch("src.services.brand_gmail_service.supabase_update", side_effect=fake_update), \
         patch("src.services.brand_gmail_service.build") as mock_build:
        mock_creds = MagicMock(
            token="expired-access-token",
            refresh_token="revoked-refresh-token",
            expiry=datetime.now(timezone.utc) - timedelta(minutes=5),
            expired=True,
        )
        mock_creds.refresh.side_effect = Exception("invalid_grant: Token has been expired or revoked.")
        MockCreds.return_value = mock_creds

        result = svc._build_service(brand)

    assert result is None
    mock_creds.refresh.assert_called_once()
    mock_build.assert_not_called()
    assert any(
        table == "brands" and data.get("gmail_connected") is False
        for table, match, data in update_calls
    )
