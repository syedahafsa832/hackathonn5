"""
Supabase GoTrue Client Tests
=============================
Covers sign_up()'s duplicate-email detection, which has to handle two
different response shapes Supabase can return for the same "this email
already exists" case (anti-enumeration: it never returns a real error).

Regression coverage for a production bug: a retry registration attempt for
an email with a pending (unconfirmed) signup came back as `user: null`
rather than `user: {..., identities: []}`, and the original check only
handled the empty-identities shape — it fell through and register() then
returned a confusing generic "Registration failed" instead of the correct
"Email already registered".
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services import supabase_gotrue  # noqa: E402
from src.services.supabase_gotrue import GoTrueError  # noqa: E402


def _fake_response(status_code, json_body):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    return resp


def test_sign_up_raises_on_empty_identities_shape():
    """The documented Supabase anti-enumeration shape: a real user.id but identities: []."""
    resp = _fake_response(200, {"user": {"id": "existing-id", "identities": []}, "session": None})
    with patch.object(supabase_gotrue._session, "post", return_value=resp):
        with pytest.raises(GoTrueError, match="Email already registered"):
            supabase_gotrue.sign_up("dup@example.com", "supersecret123")


def test_sign_up_raises_on_null_user_shape():
    """The shape that slipped through before the fix: user is null outright, no session."""
    resp = _fake_response(200, {"user": None, "session": None})
    with patch.object(supabase_gotrue._session, "post", return_value=resp):
        with pytest.raises(GoTrueError, match="Email already registered"):
            supabase_gotrue.sign_up("dup@example.com", "supersecret123")


def test_sign_up_succeeds_for_a_genuine_new_signup_pending_confirmation():
    """A real new signup: user.id is present even without a session — must NOT be
    mistaken for a duplicate."""
    resp = _fake_response(200, {"user": {"id": "new-id", "email": "new@example.com", "identities": [{"id": "i1"}]}, "session": None})
    with patch.object(supabase_gotrue._session, "post", return_value=resp):
        data = supabase_gotrue.sign_up("new@example.com", "supersecret123")
    assert data["user"]["id"] == "new-id"
    assert data["session"] is None


def test_sign_up_succeeds_with_immediate_session():
    resp = _fake_response(200, {"user": {"id": "new-id"}, "session": {"access_token": "at", "refresh_token": "rt"}})
    with patch.object(supabase_gotrue._session, "post", return_value=resp):
        data = supabase_gotrue.sign_up("new@example.com", "supersecret123")
    assert data["session"]["access_token"] == "at"


# ─── generate_recovery_link (Admin API, replaces the Send Email Hook path) ──

def test_generate_recovery_link_returns_action_link_on_success():
    action_link = "https://project.supabase.co/auth/v1/verify?token=abc&type=recovery&redirect_to=https://app.tresolv.online/reset-password"
    resp = _fake_response(200, {"action_link": action_link})
    with patch.object(supabase_gotrue, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role-key"), \
         patch.object(supabase_gotrue._session, "post", return_value=resp) as mock_post:
        result = supabase_gotrue.generate_recovery_link("merchant@example.com", redirect_to="https://app.tresolv.online/reset-password")

    assert result == action_link
    call_kwargs = mock_post.call_args
    assert call_kwargs.kwargs["headers"]["apikey"] == "fake-service-role-key"
    assert call_kwargs.kwargs["json"]["type"] == "recovery"
    assert call_kwargs.kwargs["json"]["email"] == "merchant@example.com"
    assert call_kwargs.kwargs["json"]["redirect_to"] == "https://app.tresolv.online/reset-password"


def test_generate_recovery_link_returns_none_without_service_role_key():
    with patch.object(supabase_gotrue, "SUPABASE_SERVICE_ROLE_KEY", ""):
        result = supabase_gotrue.generate_recovery_link("merchant@example.com")
    assert result is None


def test_generate_recovery_link_returns_none_on_error_response_never_raises():
    """Must not raise — the caller (request_password_reset) treats this the
    same as "no such account" and still returns the generic success
    response, so a raised exception here would either leak account
    existence via a 500 or crash the endpoint outright."""
    resp = _fake_response(422, {"msg": "Unable to validate email address"})
    with patch.object(supabase_gotrue, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role-key"), \
         patch.object(supabase_gotrue._session, "post", return_value=resp):
        result = supabase_gotrue.generate_recovery_link("not-an-account@example.com")
    assert result is None


def test_generate_recovery_link_returns_none_on_network_error():
    import requests
    with patch.object(supabase_gotrue, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role-key"), \
         patch.object(supabase_gotrue._session, "post", side_effect=requests.ConnectionError("boom")):
        result = supabase_gotrue.generate_recovery_link("merchant@example.com")
    assert result is None
