"""
System Email Service
=====================
Sends tResolv's own system/auth emails (password reset, and — since the
Supabase Send Email Hook is global across every auth email type — signup
confirmation, magic link, email change, and invite too) via a dedicated
SMTP account.

Deliberately separate from brand_gmail_service.py, which sends
customer-facing support replies through each MERCHANT's own connected
Gmail account. That's the wrong shape for these emails: a password reset
must come from tResolv itself and must work even for a tenant with no
brand or Gmail connection yet, not from a merchant's personal inbox.

SMTP credentials are read from environment variables only, never exposed
to the frontend, and never included in any exception message that gets
logged.
"""
import os
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_SERVER = os.getenv("SMTP_SERVER", "")
SMTP_PORT = int(os.getenv("SMTP_PORT") or "587")
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "") or SMTP_USERNAME
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "tResolv")

_BRAND_COLOR = "#0EA5B7"


def _is_configured() -> bool:
    return bool(SMTP_SERVER and SMTP_USERNAME and SMTP_PASSWORD and SMTP_FROM_EMAIL)


def _send(to_email: str, subject: str, html_body: str, text_body: str) -> bool:
    """Send one email via SMTP. Returns True/False — never raises past this
    boundary. On failure, logs only the exception's class name, never its
    message: smtplib exceptions can echo back the raw SMTP server
    conversation (including the AUTH exchange), which is not safe to put
    in a shared log stream, but the exception type alone is enough to
    distinguish an auth failure from a connection/TLS failure."""
    if not _is_configured():
        logger.error("[SystemEmail] SMTP is not fully configured — email not sent")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        # Port 465 is implicit TLS (the connection must be SSL from the
        # first byte); every other port (587, 25) uses STARTTLS to upgrade
        # a plaintext connection. Using the wrong mode for the port makes
        # the server reject the handshake outright.
        smtp_cls = smtplib.SMTP_SSL if SMTP_PORT == 465 else smtplib.SMTP
        with smtp_cls(SMTP_SERVER, SMTP_PORT, timeout=15) as server:
            if SMTP_PORT != 465:
                server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM_EMAIL, [to_email], msg.as_string())
        logger.info(f"[SystemEmail] Sent '{subject}' to {to_email}")
        return True
    except Exception as e:
        # Log the exception's class only (e.g. "SMTPAuthenticationError",
        # "SSLError", "TimeoutError") - enough to tell an auth failure from
        # a connection/TLS failure without str(e), which for smtplib
        # exceptions can echo back the raw SMTP server conversation
        # (including the AUTH exchange).
        logger.error(f"[SystemEmail] Delivery failed for '{subject}' to {to_email} ({type(e).__name__})")
        return False


def _shell(preheader: str, heading: str, body_html: str, action_label: str, action_url: str, footnote: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#F1F5F9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <span style="display:none;font-size:1px;color:#F1F5F9;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">{preheader}</span>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F1F5F9;padding:40px 16px;">
    <tr><td align="center">
      <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;max-width:480px;width:100%;">
        <tr><td style="padding:32px 32px 0 32px;">
          <div style="font-size:20px;font-weight:700;color:#0F172A;">
            <span style="color:{_BRAND_COLOR};">t</span>Resolv
          </div>
        </td></tr>
        <tr><td style="padding:24px 32px 8px 32px;">
          <h1 style="margin:0;font-size:20px;color:#0F172A;">{heading}</h1>
        </td></tr>
        <tr><td style="padding:8px 32px 24px 32px;font-size:14px;line-height:1.6;color:#475569;">
          {body_html}
        </td></tr>
        <tr><td style="padding:0 32px 32px 32px;">
          <a href="{action_url}" style="display:inline-block;background:{_BRAND_COLOR};color:#ffffff;text-decoration:none;font-weight:600;font-size:14px;padding:12px 24px;border-radius:6px;">{action_label}</a>
        </td></tr>
        <tr><td style="padding:0 32px 24px 32px;font-size:12px;color:#94A3B8;word-break:break-all;">
          Or copy and paste this link into your browser:<br />
          <a href="{action_url}" style="color:{_BRAND_COLOR};">{action_url}</a>
        </td></tr>
        <tr><td style="padding:16px 32px 32px 32px;border-top:1px solid #E2E8F0;font-size:12px;color:#94A3B8;">
          {footnote}
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def send_password_reset_email(to_email: str, reset_url: str) -> bool:
    """The fully-branded password reset email."""
    subject = "Reset your tResolv password"
    body_html = "You requested a password reset for your tResolv account. Click the button below to choose a new password."
    html = _shell(
        preheader="Reset your tResolv password",
        heading="Reset your password",
        body_html=body_html,
        action_label="Reset password",
        action_url=reset_url,
        footnote="This link will expire soon for your security. If you didn't request this, you can safely ignore this email — your password won't be changed.",
    )
    text = (
        "Reset your tResolv password\n\n"
        "You requested a password reset for your tResolv account.\n"
        "Open this link to choose a new password:\n\n"
        f"{reset_url}\n\n"
        "This link will expire soon for your security. If you didn't request this, "
        "you can safely ignore this email — your password won't be changed."
    )
    return _send(to_email, subject, html, text)


def send_generic_auth_email(to_email: str, subject: str, action_label: str, action_url: str) -> bool:
    """
    Fallback for non-recovery Supabase auth email types (signup confirmation,
    magic link, email change, invite) so enabling the Send Email Hook — which
    is global across every auth email type, not scopable to recovery only —
    doesn't silently break those flows. Simpler template, same sender.
    """
    body_html = f"tResolv account notification: {subject.lower()}. Click the button below to continue."
    html = _shell(
        preheader=subject,
        heading=subject,
        body_html=body_html,
        action_label=action_label,
        action_url=action_url,
        footnote="If you didn't expect this email, you can safely ignore it.",
    )
    text = f"{subject}\n\n{action_label}: {action_url}\n\nIf you didn't expect this email, you can safely ignore it."
    return _send(to_email, subject, html, text)
