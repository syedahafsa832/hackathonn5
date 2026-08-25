"""
System Email Service Tests
============================
Covers SMTP send success/failure, and the security requirements from the
password-reset-email task: SMTP credentials never appear in log output,
and delivery failures never raise past the module boundary (the caller —
the auth email hook — needs a clean True/False, not an exception carrying
SMTP internals).
"""
import os
import sys
import smtplib
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services import system_email_service  # noqa: E402


def _configured(**overrides):
    defaults = dict(
        SMTP_SERVER="smtp.example.com",
        SMTP_PORT=587,
        SMTP_USERNAME="system@tresolv.online",
        SMTP_PASSWORD="super-secret-smtp-password",
        SMTP_FROM_EMAIL="system@tresolv.online",
        SMTP_FROM_NAME="tResolv",
    )
    defaults.update(overrides)
    return defaults


def test_send_password_reset_email_success():
    with patch.multiple("src.services.system_email_service", **_configured()), \
         patch("smtplib.SMTP") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        result = system_email_service.send_password_reset_email(
            "merchant@example.com", "https://project.supabase.co/auth/v1/verify?token=abc&type=recovery&redirect_to=https://app.tresolv.online/reset-password"
        )

    assert result is True
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("system@tresolv.online", "super-secret-smtp-password")
    mock_server.sendmail.assert_called_once()
    # Headers are plain text; the multipart body is base64-encoded by
    # MIMEText, so check the header for "this is the right email" and check
    # the whole raw message (headers + encoded body) for the one thing that
    # must never appear anywhere in it regardless of encoding.
    sent_message = mock_server.sendmail.call_args[0][2]
    assert "Subject: Reset your tResolv password" in sent_message
    assert "super-secret-smtp-password" not in sent_message


def test_send_uses_implicit_ssl_on_port_465_not_starttls():
    """Zoho (and most providers) serve port 465 as implicit TLS - a plaintext
    STARTTLS handshake on that port gets rejected by the server. 587 must
    still use STARTTLS since that port is plaintext until upgraded."""
    with patch.multiple("src.services.system_email_service", **_configured(SMTP_PORT=465)), \
         patch("smtplib.SMTP_SSL") as mock_ssl_cls, \
         patch("smtplib.SMTP") as mock_plain_cls:
        mock_server = MagicMock()
        mock_ssl_cls.return_value.__enter__.return_value = mock_server

        result = system_email_service.send_password_reset_email(
            "merchant@example.com", "https://example.com/reset"
        )

    assert result is True
    mock_ssl_cls.assert_called_once_with("smtp.example.com", 465, timeout=15)
    mock_plain_cls.assert_not_called()
    mock_server.starttls.assert_not_called()
    mock_server.login.assert_called_once_with("system@tresolv.online", "super-secret-smtp-password")


def test_send_email_returns_false_when_smtp_not_configured():
    with patch.multiple("src.services.system_email_service",
                         SMTP_SERVER="", SMTP_USERNAME="", SMTP_PASSWORD="", SMTP_FROM_EMAIL=""):
        result = system_email_service.send_password_reset_email("merchant@example.com", "https://example.com/reset")

    assert result is False


def test_send_email_returns_false_on_smtp_failure_without_raising():
    with patch.multiple("src.services.system_email_service", **_configured()), \
         patch("smtplib.SMTP", side_effect=smtplib.SMTPAuthenticationError(535, b"Authentication failed")):
        result = system_email_service.send_password_reset_email("merchant@example.com", "https://example.com/reset")

    assert result is False  # never raises past this boundary


def test_smtp_credentials_never_appear_in_log_output(caplog):
    with patch.multiple("src.services.system_email_service", **_configured()), \
         patch("smtplib.SMTP", side_effect=smtplib.SMTPAuthenticationError(535, b"Authentication failed for super-secret-smtp-password")):
        with caplog.at_level("ERROR"):
            system_email_service.send_password_reset_email("merchant@example.com", "https://example.com/reset")

    all_log_text = "\n".join(r.message for r in caplog.records)
    assert "super-secret-smtp-password" not in all_log_text


def test_generic_auth_email_used_for_non_recovery_action_types():
    with patch.multiple("src.services.system_email_service", **_configured()), \
         patch("smtplib.SMTP") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        result = system_email_service.send_generic_auth_email(
            "new-signup@example.com", "Confirm your tResolv account", "Confirm email",
            "https://project.supabase.co/auth/v1/verify?token=xyz&type=signup&redirect_to=https://app.tresolv.online/login",
        )

    assert result is True
    sent_message = mock_server.sendmail.call_args[0][2]
    assert "Confirm your tResolv account" in sent_message
