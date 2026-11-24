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

    assert _recipient_token(f"{token}@inbound.tresolv.online") == token
    assert _recipient_token(f"<{token}@inbound.tresolv.online>") == token
    assert _recipient_token("hello@inbound.tresolv.online") is None
    assert _recipient_token("a" * 39 + "@inbound.tresolv.online") is None
