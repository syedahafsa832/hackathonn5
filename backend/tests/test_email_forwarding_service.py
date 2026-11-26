import email
from email.message import EmailMessage
import os
import sys

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.email_forwarding_service import (  # noqa: E402
    _recipient_token,
    forwarding_message_ids,
    parse_forwarded_email,
    _google_forwarding_confirmation_code,
    _google_forwarding_confirmation_link,
)


def _raw_email(
    *,
    sender="Alex Customer <alex@example.com>",
    subject="Where is my order?",
    message_id="<customer-1@example.com>",
    in_reply_to=None,
    references=None,
    body="Hi, can you check order #1234?",
):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "support@merchant.example"
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg["Date"] = "Tue, 24 Nov 2026 10:00:00 +0000"
    msg.set_content(body)
    return msg.as_bytes()


def test_parse_forwarded_email_preserves_original_sender_and_thread_headers():
    raw = _raw_email(
        in_reply_to="<agent-reply@tresolv.online>",
        references="<customer-1@example.com> <agent-reply@tresolv.online>",
    )

    parsed = parse_forwarded_email(raw)

    assert parsed["sender_email"] == "alex@example.com"
    assert parsed["sender_name"] == "Alex Customer"
    assert parsed["subject"] == "Where is my order?"
    assert "order #1234" in parsed["body"]
    assert parsed["message_id"] == "<customer-1@example.com>"
    assert parsed["in_reply_to"] == "<agent-reply@tresolv.online>"
    assert parsed["references"] == [
        "<customer-1@example.com>",
        "<agent-reply@tresolv.online>",
    ]
    assert parsed["received_at"].startswith("2026-11-24T10:00:00")


def test_parse_forwarded_email_falls_back_to_html_body():
    msg = EmailMessage()
    msg["From"] = "Alex <alex@example.com>"
    msg["Subject"] = "HTML only"
    msg["Message-ID"] = "<html-1@example.com>"
    msg.set_content("<p>Hello <strong>Luna</strong></p>", subtype="html")

    parsed = parse_forwarded_email(msg.as_bytes())

    assert "Hello" in parsed["body"]
    assert "Luna" in parsed["body"]


def test_forwarding_ids_are_stable_and_scoped_to_brand():
    raw = _raw_email()
    parsed = parse_forwarded_email(raw)

    first = forwarding_message_ids("brand-a", parsed, raw)
    second = forwarding_message_ids("brand-a", parsed, raw)
    other_brand = forwarding_message_ids("brand-b", parsed, raw)

    assert first == second
    assert first != other_brand
    assert first[0].startswith("fwd_")
    assert first[1].startswith("fwd_thread_")


def test_forwarding_thread_id_uses_first_reference_as_thread_root():
    first_raw = _raw_email(message_id="<first@example.com>")
    reply_raw = _raw_email(
        message_id="<reply@example.com>",
        in_reply_to="<agent-reply@tresolv.online>",
        references="<first@example.com> <agent-reply@tresolv.online>",
    )
    first = parse_forwarded_email(first_raw)
    reply = parse_forwarded_email(reply_raw)

    assert forwarding_message_ids("brand-a", first, first_raw)[1] == (
        forwarding_message_ids("brand-a", reply, reply_raw)[1]
    )


def test_recipient_token_accepts_only_opaque_hex_aliases():
    token = "a" * 40

    assert _recipient_token(f"support+{token}@inbound.tresolv.online") == token
    assert _recipient_token(f"<support+{token}@inbound.tresolv.online>") == token
    assert _recipient_token(f"hello+{token}@inbound.tresolv.online") is None
    assert _recipient_token(f"support@inbound.tresolv.online") is None
    assert _recipient_token(f"support+{token}@wrong.example") is None
    assert _recipient_token("support+" + "a" * 39 + "@inbound.tresolv.online") is None


def test_only_google_forwarding_confirmation_email_exposes_code():
    allowed = {
        "sender_email": "mail-noreply@google.com",
        "subject": "Gmail Forwarding Confirmation - Receive Mail from Your Other Accounts",
        "body": "Your confirmation code is 12345678. Enter it in Gmail.",
    }
    lookalike = {**allowed, "sender_email": "attacker@example.com"}
    unrelated_google = {**allowed, "subject": "Security alert for your Google Account"}

    assert _google_forwarding_confirmation_code(allowed) == "12345678"
    assert _google_forwarding_confirmation_code(lookalike) is None
    assert _google_forwarding_confirmation_code(unrelated_google) is None

def test_google_forwarding_code_is_extracted_from_subject_prefix():
    message = {
        "sender_email": "forwarding-noreply@google.com",
        "subject": "(#87654321) Gmail Forwarding Confirmation - Receive Mail from shop@gmail.com",
        "body": "To allow automatic forwarding, click the link below.",
    }

    assert _google_forwarding_confirmation_code(message) == "87654321"


def test_google_forwarding_code_prefers_labelled_body_code_over_unrelated_subject_digits():
    message = {
        "sender_email": "mail-noreply@google.com",
        "subject": "Gmail Forwarding Confirmation - Receive Mail from shop@gmail.com",
        "body": "Order 123456789. Confirmation code: 11223344.",
    }

    assert _google_forwarding_confirmation_code(message) == "11223344"


def test_google_forwarding_confirmation_link_is_extracted_from_plain_text():
    link = "https://mail-settings.google.com/mail/vf-ABCD1234-token"
    message = {
        "sender_email": "forwarding-noreply@google.com",
        "subject": "Gmail Forwarding Confirmation - Receive Mail from shop@gmail.com",
        "body": f"To confirm, open this link: {link}",
    }

    assert _google_forwarding_confirmation_link(message) == link


def test_google_forwarding_confirmation_link_is_extracted_from_html_href():
    link = "https://mail-settings.google.com/mail/vf-ABCD1234-token"
    msg = EmailMessage()
    msg["From"] = "Gmail Team <mail-noreply@google.com>"
    msg["Subject"] = "Gmail Forwarding Confirmation - Receive Mail from shop@gmail.com"
    msg.set_content(f'<p><a href="{link}">Confirm forwarding</a></p>', subtype="html")

    parsed = parse_forwarded_email(msg.as_bytes())

    assert _google_forwarding_confirmation_link(parsed) == link


def test_google_forwarding_confirmation_rejects_non_google_links():
    message = {
        "sender_email": "mail-noreply@google.com",
        "subject": "Gmail Forwarding Confirmation - Receive Mail from shop@gmail.com",
        "body": "https://evil.example/mail/vf-fake-token",
    }

    assert _google_forwarding_confirmation_link(message) is None

