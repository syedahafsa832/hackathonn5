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


def test_valid_access_token_is_reused_without_refresh():
    from datetime import datetime, timezone, timedelta
    brand = {"id": "brand-1", "gmail_token": "enc"}
    svc = BrandGmailService()
    expiry = datetime.now(timezone.utc) + timedelta(minutes=45)
    fake_service = MagicMock()
    with patch("src.services.brand_gmail_service.decrypt_token", return_value=json.dumps({
        "token": "valid", "refresh_token": "rt", "client_id": "cid",
        "client_secret": "cs", "expiry": expiry.isoformat(),
    })), patch("src.services.brand_gmail_service.Credentials") as MockCreds, \
         patch("src.services.brand_gmail_service.build", return_value=fake_service), \
         patch("src.services.brand_gmail_service.supabase_update") as mock_update:
        creds = MagicMock(token="valid", refresh_token="rt", expiry=expiry, expired=False)
        MockCreds.return_value = creds
        assert svc._build_service(brand) is fake_service
    creds.refresh.assert_not_called()
    mock_update.assert_not_called()


def test_send_only_refresh_writes_only_send_token_column():
    from datetime import datetime, timezone, timedelta
    brand = {"id": "brand-2", "email_ingestion_mode": "forwarding",
             "gmail_send_token": "enc", "gmail_send_connected": True}
    svc = BrandGmailService()
    updates = []
    def fake_update(table, match, data):
        updates.append(data)
        return [data]
    expired_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    with patch("src.services.brand_gmail_service.decrypt_token", return_value=json.dumps({
        "token": "old", "refresh_token": "rt", "client_id": "cid",
        "client_secret": "cs", "expiry": expired_at.isoformat(),
    })), patch("src.services.brand_gmail_service.Credentials") as MockCreds, \
         patch("src.services.brand_gmail_service.Request"), \
         patch("src.services.brand_gmail_service.supabase_update", side_effect=fake_update), \
         patch("src.services.brand_gmail_service.build"):
        creds = MagicMock(token="old", refresh_token="rt", expiry=expired_at, expired=True)
        def refresh_token(_request):
            creds.token = "new"
            creds.expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        creds.refresh.side_effect = refresh_token
        MockCreds.return_value = creds
        svc._build_service(brand)
    assert len(updates) == 1
    assert "gmail_send_token" in updates[0]
    assert "gmail_token" not in updates[0]
