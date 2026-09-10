"""
loop_risk / auto_reply_count redesign:
- default max_auto_replies raised 2 -> 5 (too aggressive for legitimate
  4-6 turn Shopify support conversations)
- a genuine human takeover (send_reply / respond_to_ticket / approve-ai)
  resets auto_reply_count/loop_risk so the budget isn't stuck forever
- a cheap deterministic repeated-reply guard (_is_repeat_ai_reply) catches
  a stuck AI generating the exact same answer again, independent of the
  turn-count ceiling - no embeddings, no LLM judge
"""
import os
import sys
import asyncio
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.workers.message_processor import _normalize_reply_text, _is_repeat_ai_reply  # noqa: E402
from src.services.email_filter_service import DEFAULT_MAX_AUTO_REPLIES  # noqa: E402
from src.api.routes.v2_email_filter import EmailFilterSettingsResponse  # noqa: E402
from src.api.routes.v2_tickets import approve_ai_response, ApproveAiRequest  # noqa: E402
from src.api.routes.tickets import send_reply, SendReplyRequest  # noqa: E402
from src.api.middleware.auth_middleware import AuthenticatedContext, UserContext, UserRole  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ── 1. Default is 5 ──────────────────────────────────────────────────────

def test_default_max_auto_replies_is_five():
    assert DEFAULT_MAX_AUTO_REPLIES == 5
    assert EmailFilterSettingsResponse().max_auto_replies == 5


# ── 2/3. Turn-count ceiling: 5 allowed, loop_risk trips at 5 (6th suppressed) ──

def test_five_replies_allowed_sixth_would_be_suppressed():
    """Same formula message_processor.py applies after a real send:
    new_count = current_count + 1; loop_risk = new_count >= max_replies.
    Turns 1-4 must not trip loop_risk; the 5th does - so a 6th message
    hits an already-loop_risk ticket and gets its send suppressed (that
    suppression path itself is covered by test_email_poller_loop_risk.py
    / the existing early_ticket.get("loop_risk") check)."""
    max_replies = 5
    results = []
    for current_count in range(5):
        new_count = current_count + 1
        results.append(new_count >= max_replies)
    assert results == [False, False, False, False, True]


# ── 6. Repeated-reply guard ──────────────────────────────────────────────

def test_normalize_collapses_whitespace_and_case():
    assert _normalize_reply_text("  Hey   Bushra!\n\nThanks.  ") == "hey bushra! thanks."


def test_repeated_ai_reply_is_detected_against_last_sent_replies():
    messages = [
        {"direction": "inbound", "body": "still need help"},
        {"direction": "outbound", "from": "AI Agent", "body": "Sorry, I can't find that order."},
    ]
    new_reply = "  sorry, I can't find that order.  "
    assert _is_repeat_ai_reply(new_reply, messages) is True


def test_different_ai_reply_is_not_flagged_as_repeat():
    messages = [{"direction": "outbound", "from": "AI Agent", "body": "Sorry, I can't find that order."}]
    assert _is_repeat_ai_reply("Sure, order #1009 shipped yesterday!", messages) is False


def test_short_but_different_reply_is_not_flagged_just_for_being_short():
    """"Thanks!" must not falsely trip the guard just because it's short -
    only an EXACT normalized match against a recently-sent AI reply does."""
    messages = [{"direction": "outbound", "from": "AI Agent", "body": "You're welcome!"}]
    assert _is_repeat_ai_reply("Thanks!", messages) is False


def test_draft_and_internal_note_entries_are_not_compared():
    """Only direction='outbound' (actually sent) AI messages count - a draft
    that was never sent, or a CS-only internal note, must never suppress a
    genuinely new send."""
    messages = [
        {"direction": "draft", "from": "AI Agent", "body": "Hello there!"},
        {"direction": "internal_note", "from": "agent@x.com", "body": "Hello there!"},
    ]
    assert _is_repeat_ai_reply("Hello there!", messages) is False


# ── 5. Human reset (approve-ai) ──────────────────────────────────────────

BRAND_ID = "brand-1"
TICKET_ID = "ticket-1"


def _context():
    return AuthenticatedContext(
        user=UserContext(user_id="user-1", supabase_auth_id="auth-1",
                          organization_id="org-1", email="agent@example.com",
                          role=UserRole.ADMIN, brands=[BRAND_ID]),
        organization=None, brand_ids=[BRAND_ID],
    )


def test_approve_ai_resets_auto_reply_count_and_loop_risk():
    ticket = {
        "id": TICKET_ID, "brand_id": BRAND_ID, "store_id": BRAND_ID,
        "customer_email": "customer@example.com", "subject": "Still stuck",
        "ai_draft": "Here's an update.", "ai_response": None,
        "human_approved": False, "response_sent": False,
        "auto_reply_count": 5, "loop_risk": True,
        "messages": [],
    }
    captured = {}

    def fake_select(table, params=None):
        if table == "tickets":
            return [ticket]
        if table == "brands":
            return [{"id": BRAND_ID, "gmail_connected": True}]
        return []

    def fake_update(table, match, fields):
        captured.update(fields)
        return [{**ticket, **fields}]

    with patch("src.api.routes.v2_tickets.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_tickets.supabase_update", side_effect=fake_update), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email",
               new=AsyncMock(return_value={"success": True})):
        run(approve_ai_response(TICKET_ID, ApproveAiRequest(), _context()))

    assert captured["auto_reply_count"] == 0
    assert captured["loop_risk"] is False


class _Tenant:
    def __init__(self):
        self.tenant_id = "tenant-1"


def test_send_reply_resets_auto_reply_count_and_loop_risk():
    ticket = {
        "id": TICKET_ID, "store_id": BRAND_ID, "customer_email": "customer@example.com",
        "subject": "Still stuck", "ai_draft": None, "ai_reply": None,
        "auto_reply_count": 5, "loop_risk": True, "messages": [],
        "gmail_thread_id": "thread-1",
    }
    captured = {}

    def fake_select(table, params=None):
        if table == "tickets":
            return [ticket]
        if table == "brands":
            return [{"id": BRAND_ID, "gmail_connected": True}]
        return []

    def fake_update(table, match, fields):
        captured.update(fields)
        return [{**ticket, **fields}]

    with patch("src.api.routes.tickets.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.tickets.supabase_update", side_effect=fake_update), \
         patch("src.api.routes.tickets._assert_ticket_access", new=AsyncMock(return_value=ticket)), \
         patch("src.api.routes.tickets._invalidate_tickets_cache"), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email",
               new=AsyncMock(return_value={"success": True})):
        run(send_reply(TICKET_ID, SendReplyRequest(body="Hi, here's a manual update."), _Tenant()))

    assert captured["auto_reply_count"] == 0
    assert captured["loop_risk"] is False
