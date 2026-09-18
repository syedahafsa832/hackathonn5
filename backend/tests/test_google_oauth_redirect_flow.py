"""
Google account sign-in: origin-independent redirect flow.

Root cause of the "400: malformed request" from https://reports.constants.io:
the old flow used Google Identity Services' client-side
google.accounts.id.initialize/renderButton, which requires the exact
calling-page origin to be pre-registered in Google Cloud Console's
"Authorized JavaScript origins" - a hard Google requirement that breaks
from any origin not on that manually-maintained list.

Fix: a backend-mediated OAuth Authorization Code redirect flow
(auth_service.build_google_oauth_url / handle_google_oauth_callback) that
only ever needs ONE Google-registered redirect URI (this backend's own
fixed callback), regardless of the frontend origin the user started from.
The return-address is validated against the same allowlist that already
protects the dashboard API (cors.py's _get_allowed_origins), so an
untrusted return_to can never become an open redirect.

This is account authentication ONLY - not the separate Gmail-inbox
connection OAuth (brand_gmail_service.py).
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("API_BASE_URL", "https://backend.tresolv.online")

import pytest  # noqa: E402

from src.services.auth_service import AuthService  # noqa: E402
from src.api.middleware.cors import _PRODUCTION_ORIGIN  # noqa: E402


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# build_google_oauth_url: origin allowlisting + canonical redirect_uri
# ---------------------------------------------------------------------------

def test_untrusted_origin_falls_back_to_canonical_production_origin():
    """An arbitrary domain (e.g. reports.constants.io) must never be echoed
    back into the OAuth flow as a return address."""
    url = AuthService().build_google_oauth_url("https://reports.constants.io")
    import urllib.parse
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    import jwt
    from src.services.auth_service import JWT_SECRET
    payload = jwt.decode(state, JWT_SECRET, algorithms=["HS256"])
    assert payload["return_to"] == _PRODUCTION_ORIGIN


def test_allowed_origin_is_preserved():
    url = AuthService().build_google_oauth_url("http://localhost:5173")
    import urllib.parse, jwt
    from src.services.auth_service import JWT_SECRET
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    payload = jwt.decode(state, JWT_SECRET, algorithms=["HS256"])
    assert payload["return_to"] == "http://localhost:5173"


def test_redirect_uri_is_the_one_canonical_backend_callback_regardless_of_origin():
    """The redirect_uri Google validates must be fixed - never derived from
    the caller-supplied return_to - so only ONE URI ever needs registering
    in Google Cloud Console."""
    import urllib.parse
    url_a = AuthService().build_google_oauth_url("http://localhost:5173")
    url_b = AuthService().build_google_oauth_url("https://reports.constants.io")
    redirect_a = urllib.parse.parse_qs(urllib.parse.urlparse(url_a).query)["redirect_uri"][0]
    redirect_b = urllib.parse.parse_qs(urllib.parse.urlparse(url_b).query)["redirect_uri"][0]
    assert redirect_a == redirect_b == "https://backend.tresolv.online/api/v1/auth/google/callback"


# ---------------------------------------------------------------------------
# handle_google_oauth_callback: state verification + existing google_auth() reuse
# ---------------------------------------------------------------------------

def _fake_token_response():
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"id_token": "fake-id-token"}
    return resp


def test_valid_code_and_state_signs_in_and_returns_the_original_origin():
    service = AuthService()
    state = service.build_google_oauth_url("http://localhost:5173")
    import urllib.parse
    state_val = urllib.parse.parse_qs(urllib.parse.urlparse(state).query)["state"][0]

    with patch("requests.post", return_value=_fake_token_response()), \
         patch.object(AuthService, "google_auth", new=AsyncMock(return_value={
             "success": True, "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
         })):
        result = _run(service.handle_google_oauth_callback("auth-code", state_val))

    assert result["success"] is True
    assert result["return_to"] == "http://localhost:5173"
    assert result["access_token"] == "at"


def test_tampered_state_fails_safely_without_crashing():
    service = AuthService()
    result = _run(service.handle_google_oauth_callback("auth-code", "not-a-real-jwt"))
    assert result["success"] is False
    assert result["return_to"] == _PRODUCTION_ORIGIN


def test_expired_state_fails_safely():
    import jwt
    from datetime import datetime, timezone, timedelta
    from src.services.auth_service import JWT_SECRET
    expired_state = jwt.encode(
        {"return_to": "http://localhost:5173", "nonce": "x", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        JWT_SECRET, algorithm="HS256",
    )
    result = _run(AuthService().handle_google_oauth_callback("auth-code", expired_state))
    assert result["success"] is False


def test_token_exchange_failure_returns_safe_error_not_an_exception():
    service = AuthService()
    url = service.build_google_oauth_url("http://localhost:5173")
    import urllib.parse
    state_val = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]

    with patch("requests.post", side_effect=Exception("network error")):
        result = _run(service.handle_google_oauth_callback("auth-code", state_val))

    assert result["success"] is False
    assert result["return_to"] == "http://localhost:5173"
