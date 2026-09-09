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
