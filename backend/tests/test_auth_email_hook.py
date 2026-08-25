"""
Auth Email Hook Tests
=======================
Covers the Supabase Send Email Hook receiver: Standard Webhooks signature
verification (valid/invalid/missing/replayed-old), payload routing by
email_action_type, delivery failure handling, and the logging-safety
requirements from the password-reset task — token_hash/redirect_to/the
full constructed verify URL must never appear in any log line.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://project-ref.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.api.routes.auth_email_hook import router  # noqa: E402

TEST_SECRET_RAW = b"0123456789abcdef0123456789abcdef"
TEST_SECRET = "whsec_" + base64.b64encode(TEST_SECRET_RAW).decode()

app = FastAPI()
app.include_router(router, prefix="/api/v1")
client = TestClient(app)


def _sign(body: bytes, secret_raw: bytes = TEST_SECRET_RAW, webhook_id: str = "msg_test123", timestamp: int = None):
    ts = timestamp if timestamp is not None else int(time.time())
    signed_content = f"{webhook_id}.{ts}.{body.decode('utf-8')}"
    sig = base64.b64encode(hmac.new(secret_raw, signed_content.encode("utf-8"), hashlib.sha256).digest()).decode()
    return {
        "webhook-id": webhook_id,
        "webhook-timestamp": str(ts),
        "webhook-signature": f"v1,{sig}",
    }


def _recovery_payload(email="merchant@example.com", token_hash="the-real-token-hash-abc123", redirect_to="https://app.tresolv.online/reset-password"):
    return {
        "user": {"id": "user-1", "email": email},
        "email_data": {
            "token_hash": token_hash,
            "redirect_to": redirect_to,
            "email_action_type": "recovery",
            "site_url": "https://app.tresolv.online",
        },
    }


@pytest.fixture(autouse=True)
def _configured_secret():
    with patch("src.api.routes.auth_email_hook.SEND_EMAIL_HOOK_SECRET", TEST_SECRET), \
         patch("src.api.routes.auth_email_hook.SUPABASE_URL", "https://project-ref.supabase.co"):
        yield


# ─── Signature verification ─────────────────────────────────────────────────

def test_valid_signature_is_accepted_and_email_is_sent():
    body = json.dumps(_recovery_payload()).encode()
    headers = _sign(body)

    with patch("src.services.system_email_service.send_password_reset_email", return_value=True) as mock_send:
        resp = client.post("/api/v1/auth/email-hook", content=body, headers=headers)

    assert resp.status_code == 200
    mock_send.assert_called_once()
    assert mock_send.call_args[0][0] == "merchant@example.com"


def test_valid_signature_accepted_with_supabase_versioned_secret_format():
    """Supabase's Send Email Hook UI requires the secret as
    "v1,whsec_<base64>" (a version prefix ahead of the Standard Webhooks
    "whsec_" prefix) - it rejects a bare "whsec_..." secret at hook-creation
    time. Verification must strip both prefixes, not just "whsec_"."""
    versioned_secret = "v1," + TEST_SECRET
    body = json.dumps(_recovery_payload()).encode()
    headers = _sign(body)

    with patch("src.api.routes.auth_email_hook.SEND_EMAIL_HOOK_SECRET", versioned_secret), \
         patch("src.services.system_email_service.send_password_reset_email", return_value=True) as mock_send:
        resp = client.post("/api/v1/auth/email-hook", content=body, headers=headers)

    assert resp.status_code == 200
    mock_send.assert_called_once()


def test_invalid_signature_is_rejected():
    body = json.dumps(_recovery_payload()).encode()
    headers = _sign(body, secret_raw=b"wrong-secret-entirely-00000000000")

    with patch("src.services.system_email_service.send_password_reset_email") as mock_send:
        resp = client.post("/api/v1/auth/email-hook", content=body, headers=headers)

    assert resp.status_code == 401
    mock_send.assert_not_called()


def test_missing_signature_headers_are_rejected():
    body = json.dumps(_recovery_payload()).encode()

    with patch("src.services.system_email_service.send_password_reset_email") as mock_send:
        resp = client.post("/api/v1/auth/email-hook", content=body)

    assert resp.status_code == 401
    mock_send.assert_not_called()


def test_replayed_old_timestamp_is_rejected():
    body = json.dumps(_recovery_payload()).encode()
    stale_timestamp = int(time.time()) - 3600  # 1 hour old — past the 5-minute tolerance
    headers = _sign(body, timestamp=stale_timestamp)

    with patch("src.services.system_email_service.send_password_reset_email") as mock_send:
        resp = client.post("/api/v1/auth/email-hook", content=body, headers=headers)

    assert resp.status_code == 401
    mock_send.assert_not_called()


def test_unconfigured_secret_rejects_everything():
    body = json.dumps(_recovery_payload()).encode()
    headers = _sign(body)

    with patch("src.api.routes.auth_email_hook.SEND_EMAIL_HOOK_SECRET", ""):
        resp = client.post("/api/v1/auth/email-hook", content=body, headers=headers)

    assert resp.status_code == 500


# ─── Routing by email_action_type ───────────────────────────────────────────

def test_recovery_action_type_uses_the_branded_password_reset_template():
    body = json.dumps(_recovery_payload()).encode()
    headers = _sign(body)

    with patch("src.services.system_email_service.send_password_reset_email", return_value=True) as mock_recovery, \
         patch("src.services.system_email_service.send_generic_auth_email") as mock_generic:
        resp = client.post("/api/v1/auth/email-hook", content=body, headers=headers)

    assert resp.status_code == 200
    mock_recovery.assert_called_once()
    mock_generic.assert_not_called()


def test_signup_action_type_uses_the_generic_template_not_recovery():
    """The hook is global across all Supabase auth email types -- enabling
    it must not silently break signup confirmation."""
    payload = _recovery_payload()
    payload["email_data"]["email_action_type"] = "signup"
    body = json.dumps(payload).encode()
    headers = _sign(body)

    with patch("src.services.system_email_service.send_password_reset_email") as mock_recovery, \
         patch("src.services.system_email_service.send_generic_auth_email", return_value=True) as mock_generic:
        resp = client.post("/api/v1/auth/email-hook", content=body, headers=headers)

    assert resp.status_code == 200
    mock_recovery.assert_not_called()
    mock_generic.assert_called_once()


def test_constructed_verify_url_has_correct_shape():
    body = json.dumps(_recovery_payload(token_hash="abc123", redirect_to="https://app.tresolv.online/reset-password")).encode()
    headers = _sign(body)

    with patch("src.services.system_email_service.send_password_reset_email", return_value=True) as mock_send:
        client.post("/api/v1/auth/email-hook", content=body, headers=headers)

    verify_url = mock_send.call_args[0][1]
    assert verify_url.startswith("https://project-ref.supabase.co/auth/v1/verify?")
    assert "token=abc123" in verify_url
    assert "type=recovery" in verify_url
    assert "redirect_to=https%3A%2F%2Fapp.tresolv.online%2Freset-password" in verify_url


# ─── Missing fields / delivery failure ──────────────────────────────────────

def test_missing_required_fields_returns_400():
    payload = {"user": {"email": "merchant@example.com"}, "email_data": {"email_action_type": "recovery"}}  # no token_hash
    body = json.dumps(payload).encode()
    headers = _sign(body)

    resp = client.post("/api/v1/auth/email-hook", content=body, headers=headers)

    assert resp.status_code == 400


def test_email_delivery_failure_is_logged_but_hook_still_returns_2xx(caplog):
    """Supabase enforces a hard 5-second response budget on this hook - an
    SMTP send (connection + TLS + auth + send) can easily exceed that, so
    the send happens in a background task and this endpoint returns 2xx as
    soon as the request is verified and valid, not once delivery finishes.
    A delivery failure is a diagnosable log line, not a failed hook
    response - returning 500 here would make Supabase treat the entire
    recovery/signup/etc. operation as failed even though the SMTP failure
    is on our side, not something retrying the auth operation would fix."""
    body = json.dumps(_recovery_payload()).encode()
    headers = _sign(body)

    with patch("src.services.system_email_service.send_password_reset_email", return_value=False):
        with caplog.at_level("ERROR"):
            resp = client.post("/api/v1/auth/email-hook", content=body, headers=headers)

    assert resp.status_code == 200
    assert any("Delivery failed" in r.message for r in caplog.records)


# ─── Logging safety ──────────────────────────────────────────────────────────

def test_token_hash_and_redirect_to_and_full_url_never_appear_in_logs(caplog):
    body = json.dumps(_recovery_payload(
        token_hash="SUPER-SENSITIVE-TOKEN-HASH-99999",
        redirect_to="https://app.tresolv.online/reset-password?extra=data",
    )).encode()
    headers = _sign(body)

    with patch("src.services.system_email_service.send_password_reset_email", return_value=True):
        with caplog.at_level("DEBUG"):
            client.post("/api/v1/auth/email-hook", content=body, headers=headers)

    all_log_text = "\n".join(r.message for r in caplog.records)
    assert "SUPER-SENSITIVE-TOKEN-HASH-99999" not in all_log_text
    assert "redirect_to=" not in all_log_text
    assert "/auth/v1/verify?token=" not in all_log_text
