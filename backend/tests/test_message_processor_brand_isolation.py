"""
Cross-brand isolation regression tests for backend/src/workers/message_processor.py.

Confirmed findings fixed here:
  1. STAGE 1.5 (email thread dedup) looked up an existing ticket by
     `gmail_thread_id` alone, with no `store_id` filter — if a thread id
     were ever reused/collided across two brands' Gmail connections, a
     message from Brand A would be silently appended onto Brand B's ticket.
  2. `_check_thread_override` matched an active human-takeover override
     purely on customer_email + normalized subject, across ALL tenants'
     overrides — two different brands sharing a customer email and a
     generic subject line ("Order issue") could have Brand A's human
     takeover incorrectly suppress Brand B's unrelated AI auto-reply.
  3. Stage 2.5's tenant-resolution fallback used to pick "the first active
     tenant in the whole system" whenever a real brand had no tenant_id set
     — now it fails closed (leaves tenant_id unset) for a real brand,
     and only uses that fallback for the single-tenant dev placeholder.
"""
import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402

processor = UnifiedMessageProcessor()

BRAND_A = "brand-a"
BRAND_B = "brand-b"


# ─── 1. Thread dedup must be scoped to the message's own brand ────────────

def test_thread_dedup_query_is_scoped_to_store_id():
    """The Supabase call itself must include store_id, not just gmail_thread_id —
    this is the actual query UnifiedMessageProcessor issues at STAGE 1.5."""
    captured = {}

    def fake_select(table, params=None):
        if table == "tickets":
            captured["params"] = params
        return []

    with patch("src.workers.message_processor.supabase_select", side_effect=fake_select), \
         patch("src.workers.message_processor.supabase_service") as mock_service:
        mock_service.get_system_settings.side_effect = Exception("stop early")
        mock_service.create_ticket.side_effect = Exception("no-op")
        try:
            asyncio.run(processor.process_message("gmail", {
                "channel": "email",
                "customer_email": "shared@example.com",
                "customer_name": "Shared Customer",
                "content": "Where is my order?",
                "subject": "Order issue",
                "gmail_thread_id": "thread-123",
                "store_id": BRAND_A,
            }))
        except Exception:
            pass

    assert "params" in captured, "thread-dedup lookup on 'tickets' was never issued"
    assert captured["params"].get("gmail_thread_id") == "eq.thread-123"
    assert captured["params"].get("store_id") == f"eq.{BRAND_A}", (
        "thread-dedup lookup must filter by store_id — otherwise a colliding "
        "gmail_thread_id from a different brand's mailbox could append a "
        "message onto the wrong brand's ticket"
    )


# ─── 2. _check_thread_override must not match across brands ───────────────

def test_override_check_ignores_other_brands_matching_ticket():
    """Brand A has an active human takeover on a ticket with the same
    customer email + subject as Brand B's unrelated ticket. Brand B's AI
    auto-reply must not be suppressed by Brand A's takeover."""
    override_row = {"conversation_id": "ticket-a-1", "active": True}
    ticket_a = {"id": "ticket-a-1", "customer_email": "shared@example.com",
                "subject": "Order issue", "store_id": BRAND_A}

    def fake_select(table, params=None):
        if table == "conversation_overrides":
            return [override_row]
        return []

    async def fake_get_ticket_by_id(ticket_id):
        return ticket_a if ticket_id == "ticket-a-1" else None

    with patch("src.workers.message_processor.supabase_select", side_effect=fake_select), \
         patch("src.workers.message_processor.supabase_service") as mock_service:
        mock_service.get_ticket_by_id.side_effect = fake_get_ticket_by_id
        result_for_brand_b = asyncio.run(
            processor._check_thread_override("shared@example.com", "Order issue", BRAND_B)
        )
        result_for_brand_a = asyncio.run(
            processor._check_thread_override("shared@example.com", "Order issue", BRAND_A)
        )

    assert result_for_brand_b is False, "Brand A's takeover must not suppress Brand B's reply"
    assert result_for_brand_a is True, "Brand A's own takeover must still apply to Brand A"


def test_override_check_still_works_when_store_id_unknown():
    """Backward-compatible: if store_id can't be determined, fall back to
    the pre-fix email+subject match rather than refusing to ever match."""
    override_row = {"conversation_id": "ticket-a-1", "active": True}
    ticket_a = {"id": "ticket-a-1", "customer_email": "shared@example.com",
                "subject": "Order issue", "store_id": BRAND_A}

    def fake_select(table, params=None):
        if table == "conversation_overrides":
            return [override_row]
        return []

    async def fake_get_ticket_by_id(ticket_id):
        return ticket_a if ticket_id == "ticket-a-1" else None

    with patch("src.workers.message_processor.supabase_select", side_effect=fake_select), \
         patch("src.workers.message_processor.supabase_service") as mock_service:
        mock_service.get_ticket_by_id.side_effect = fake_get_ticket_by_id
        result = asyncio.run(
            processor._check_thread_override("shared@example.com", "Order issue", None)
        )

    assert result is True


# ─── 3. brand_message_processor's dead "first active brand" fallback ──────

def test_brand_message_processor_fails_closed_with_no_identifying_info():
    from src.workers.brand_message_processor import BrandMessageProcessor

    bmp = BrandMessageProcessor()

    def fake_select(table, params=None):
        # Would previously return "the first active brand" here — must now
        # never be queried/returned for an unidentified message.
        if table == "brands":
            return [{"id": "some-other-brand", "is_active": True}]
        return []

    with patch("src.workers.brand_message_processor.supabase_select", side_effect=fake_select), \
         patch.dict(os.environ, {"SUPPORT_EMAIL_ADDRESS": ""}):
        result = asyncio.run(bmp._find_brand({"content": "hello"}))

    assert result is None
