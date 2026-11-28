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



# ---- Multi-tenant address allocation and lifecycle regression tests ----

def test_setup_allocates_one_persistent_address_per_brand(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import patch
    from src.api.routes import brand_gmail

    monkeypatch.setenv("INBOUND_EMAIL_DOMAIN", "inbound.tresolv.online")
    monkeypatch.setenv("INBOUND_EMAIL_LOCAL_PART", "support")
    store = {
        "brand-a": {"id": "brand-a", "tenant_id": "tenant-a", "is_active": True, "email_ingestion_mode": "gmail_polling"},
        "brand-b": {"id": "brand-b", "tenant_id": "tenant-b", "is_active": True, "email_ingestion_mode": "gmail_polling"},
    }

    def owned(brand_id, tenant):
        row = store[brand_id]
        if row["tenant_id"] != tenant.tenant_id:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Brand not found")
        return dict(row)

    def update(table, filters, data):
        row = store[filters["id"].removeprefix("eq.")]
        if filters.get("is_active") == "eq.true" and not row["is_active"]:
            return {}
        if filters.get("inbound_email_token") == "is.null" and row.get("inbound_email_token"):
            return {}
        row.update(data)
        return dict(row)

    with patch.object(brand_gmail, "_get_owned_brand", side_effect=owned), \
         patch.object(brand_gmail, "supabase_update", side_effect=update), \
         patch.object(brand_gmail.secrets, "token_hex", side_effect=["a" * 40, "b" * 40]):
        first = asyncio.run(brand_gmail.setup_email_forwarding(
            "brand-a", tenant=SimpleNamespace(tenant_id="tenant-a")
        ))
        repeated = asyncio.run(brand_gmail.setup_email_forwarding(
            "brand-a", tenant=SimpleNamespace(tenant_id="tenant-a")
        ))
        second_tenant = asyncio.run(brand_gmail.setup_email_forwarding(
            "brand-b", tenant=SimpleNamespace(tenant_id="tenant-b")
        ))

    assert first["forwarding_address"] == repeated["forwarding_address"]
    assert first["forwarding_address"] == "support+" + "a" * 40 + "@inbound.tresolv.online"
    assert second_tenant["forwarding_address"] == "support+" + "b" * 40 + "@inbound.tresolv.online"
    assert first["forwarding_address"] != second_tenant["forwarding_address"]


def test_setup_race_returns_the_token_persisted_by_the_winning_request(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import patch
    from src.api.routes import brand_gmail

    monkeypatch.setenv("INBOUND_EMAIL_DOMAIN", "inbound.tresolv.online")
    store = {
        "id": "brand-a",
        "tenant_id": "tenant-a",
        "is_active": True,
        "inbound_email_token": "c" * 40,
        "inbound_email_enabled": True,
        "email_ingestion_mode": "gmail_polling",
    }
    stale_reads = 0

    def owned(brand_id, tenant):
        nonlocal stale_reads
        stale_reads += 1
        # The first read models both HTTP requests seeing a brand before
        # either address exists. The second read sees the winner's DB write.
        if stale_reads == 1:
            return {k: v for k, v in store.items() if k != "inbound_email_token" and k != "inbound_email_enabled"}
        return dict(store)

    def update(table, filters, data):
        if filters.get("inbound_email_token") == "is.null" and store.get("inbound_email_token"):
            return {}
        store.update(data)
        return dict(store)

    with patch.object(brand_gmail, "_get_owned_brand", side_effect=owned), \
         patch.object(brand_gmail, "supabase_update", side_effect=update), \
         patch.object(brand_gmail.secrets, "token_hex", return_value="d" * 40):
        result = asyncio.run(brand_gmail.setup_email_forwarding(
            "brand-a", tenant=SimpleNamespace(tenant_id="tenant-a")
        ))

    assert result["forwarding_address"] == "support+" + "c" * 40 + "@inbound.tresolv.online"
    assert store["inbound_email_token"] == "c" * 40


def test_forwarding_ingestion_rejects_deactivated_brands_even_if_old_token_is_enabled(monkeypatch):
    import asyncio
    from unittest.mock import patch
    from src.services import email_forwarding_service

    token = "e" * 40
    monkeypatch.setenv("INBOUND_EMAIL_DOMAIN", "inbound.tresolv.online")
    monkeypatch.setenv("INBOUND_EMAIL_LOCAL_PART", "support")
    captured = {}

    def select(table, params):
        captured.update(params)
        return []

    with patch.object(email_forwarding_service, "supabase_select", side_effect=select):
        result = asyncio.run(email_forwarding_service.process_forwarded_email(
            b"raw message is not parsed when no active brand matches",
            "support+" + token + "@inbound.tresolv.online",
        ))

    assert result == {"accepted": False, "status": "unknown_recipient"}
    assert captured["is_active"] == "eq.true"


def test_soft_deleting_brand_disables_its_inbound_forwarding():
    import asyncio
    from unittest.mock import patch
    from src.services.brand_manager import BrandManager

    manager = BrandManager()
    with patch("src.services.brand_manager.supabase_update", return_value={}) as update:
        result = asyncio.run(manager.delete_brand("brand-a", soft_delete=True))

    assert result["success"] is True
    updates = update.call_args.args[2]
    assert updates["is_active"] is False
    assert updates["inbound_email_enabled"] is False
    assert updates["email_ingestion_mode"] == "gmail_polling"
