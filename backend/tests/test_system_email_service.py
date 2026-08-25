"""
System Email Service Tests
============================
Covers Resend API send success/failure, and the security requirements
from the password-reset-email task: the API key never appears in log
output, and delivery failures never raise past the module boundary (the
caller — the auth email hook — needs a clean True/False, not an
exception carrying HTTP/network internals).
"""
import os
import sys
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services import system_email_service  # noqa: E402


def _configured(**overrides):
    defaults = dict(
        RESEND_API_KEY="re_test_super_secret_api_key",
        SYSTEM_EMAIL_FROM_EMAIL="system@tresolv.online",
        SYSTEM_EMAIL_FROM_NAME="tResolv",
    )
    defaults.update(overrides)
    return defaults


def _mock_response(status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    return resp


def test_send_password_reset_email_success():
    with patch.multiple("src.services.system_email_service", **_configured()), \
         patch("requests.post", return_value=_mock_response(200)) as mock_post:
        result = system_email_service.send_password_reset_email(
            "merchant@example.com", "https://project.supabase.co/auth/v1/verify?token=abc&type=recovery&redirect_to=https://app.tresolv.online/reset-password"
        )

    assert result is True
    mock_post.assert_called_once()
    call = mock_post.call_args
    assert call.args[0] == "https://api.resend.com/emails"
    assert call.kwargs["headers"]["Authorization"] == "Bearer re_test_super_secret_api_key"
    body = call.kwargs["json"]
    assert body["to"] == ["merchant@example.com"]
    assert body["subject"] == "Reset your tResolv password"
    assert body["from"] == "tResolv <system@tresolv.online>"


def test_send_email_returns_false_when_not_configured():
    with patch.multiple("src.services.system_email_service", RESEND_API_KEY="", SYSTEM_EMAIL_FROM_EMAIL=""):
        result = system_email_service.send_password_reset_email("merchant@example.com", "https://example.com/reset")

    assert result is False


def test_send_email_returns_false_on_http_error_without_raising():
    with patch.multiple("src.services.system_email_service", **_configured()), \
         patch("requests.post", return_value=_mock_response(401)):
        result = system_email_service.send_password_reset_email("merchant@example.com", "https://example.com/reset")

    assert result is False  # never raises past this boundary


def test_send_email_returns_false_on_network_exception_without_raising():
    with patch.multiple("src.services.system_email_service", **_configured()), \
         patch("requests.post", side_effect=requests.exceptions.Timeout("connect timeout")):
        result = system_email_service.send_password_reset_email("merchant@example.com", "https://example.com/reset")

    assert result is False


def test_send_logs_http_status_and_exception_class_but_not_api_key(caplog):
    """Diagnosable-but-safe: knowing it was a 401 (vs. a network Timeout) is
    useful for debugging delivery failures, but the API key itself must
    never appear in logs regardless of failure mode."""
    with patch.multiple("src.services.system_email_service", **_configured()), \
         patch("requests.post", return_value=_mock_response(401)):
        with caplog.at_level("ERROR"):
            system_email_service.send_password_reset_email("merchant@example.com", "https://example.com/reset")

    all_log_text = "\n".join(r.message for r in caplog.records)
    assert "401" in all_log_text
    assert "re_test_super_secret_api_key" not in all_log_text

    caplog.clear()
    with patch.multiple("src.services.system_email_service", **_configured()), \
         patch("requests.post", side_effect=requests.exceptions.Timeout("connect timeout")):
        with caplog.at_level("ERROR"):
            system_email_service.send_password_reset_email("merchant@example.com", "https://example.com/reset")

    all_log_text = "\n".join(r.message for r in caplog.records)
    assert "Timeout" in all_log_text
    assert "re_test_super_secret_api_key" not in all_log_text


def test_generic_auth_email_used_for_non_recovery_action_types():
    with patch.multiple("src.services.system_email_service", **_configured()), \
         patch("requests.post", return_value=_mock_response(200)) as mock_post:
        result = system_email_service.send_generic_auth_email(
            "new-signup@example.com", "Confirm your tResolv account", "Confirm email",
            "https://project.supabase.co/auth/v1/verify?token=xyz&type=signup&redirect_to=https://app.tresolv.online/login",
        )

    assert result is True
    assert mock_post.call_args.kwargs["json"]["subject"] == "Confirm your tResolv account"
