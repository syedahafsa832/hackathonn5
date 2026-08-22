"""
CSAT email trigger hardening (Phase 3).

The old trigger polled on tickets.status in (auto_resolved,resolved) +
updated_at in a rolling window - fragile, since status flips on almost
every turn and a manual reply also sets status='resolved' mid-thread.
Fixed by keying off tickets.resolved_at (a real, previously-unpopulated-by-
automation column; only POST /{id}/close ever set it before this change),
formalizing csat_sent/csat_sent_at (migration 045), an atomic claim before
sending (mirrors actions_service.approve_action's race fix), and a
per-customer 30-day cooldown. Nothing here calls an LLM - eligibility is
pure Python over already-known ticket fields.
"""
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.channels.email_poller import EmailPoller  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _ticket(**overrides):
    t = {
        "id": "ticket-1",
        "store_id": "brand-1",
        "customer_email": "jane@example.com",
        "gmail_thread_id": "thread-1",
        "resolved_at": (NOW - timedelta(minutes=45)).isoformat(),
        "csat_sent": False,
        "messages": [{"direction": "inbound", "body": "hi"}, {"direction": "outbound", "body": "here's your answer"}],
    }
    t.update(overrides)
    return t


# ── Eligibility (pure function) ──────────────────────────────────────────

def test_freshly_resolved_ticket_is_eligible():
    assert EmailPoller._is_ticket_csat_eligible(_ticket(), NOW) is True


def test_ticket_resolved_too_recently_is_not_eligible():
    assert EmailPoller._is_ticket_csat_eligible(_ticket(resolved_at=(NOW - timedelta(minutes=5)).isoformat()), NOW) is False


def test_ticket_resolved_too_long_ago_is_not_eligible():
    assert EmailPoller._is_ticket_csat_eligible(_ticket(resolved_at=(NOW - timedelta(hours=3)).isoformat()), NOW) is False


def test_ticket_without_resolved_at_is_not_eligible():
    assert EmailPoller._is_ticket_csat_eligible(_ticket(resolved_at=None), NOW) is False


def test_already_sent_ticket_is_not_eligible():
    assert EmailPoller._is_ticket_csat_eligible(_ticket(csat_sent=True), NOW) is False


def test_abandoned_one_sided_conversation_is_not_eligible():
    assert EmailPoller._is_ticket_csat_eligible(_ticket(messages=[{"direction": "inbound", "body": "hi"}]), NOW) is False


def test_abandoned_empty_conversation_is_not_eligible():
    assert EmailPoller._is_ticket_csat_eligible(_ticket(messages=[]), NOW) is False


def test_messages_as_json_string_is_parsed():
    import json
    msgs = json.dumps([{"direction": "inbound", "body": "hi"}, {"direction": "outbound", "body": "answer"}])
    assert EmailPoller._is_ticket_csat_eligible(_ticket(messages=msgs), NOW) is True


# ── Per-customer cooldown ─────────────────────────────────────────────────

def test_cooldown_blocks_a_customer_surveyed_recently():
    with patch("src.channels.email_poller.supabase_select", return_value=[{"id": "other-ticket"}]) as mock_select:
        result = run(EmailPoller._customer_in_csat_cooldown("jane@example.com", "brand-1", NOW))
    assert result is True
    args, _ = mock_select.call_args
    assert args[0] == "tickets"
    assert args[1]["customer_email"] == "eq.jane@example.com"
    assert args[1]["store_id"] == "eq.brand-1"


def test_cooldown_allows_a_customer_with_no_recent_survey():
    with patch("src.channels.email_poller.supabase_select", return_value=[]):
        result = run(EmailPoller._customer_in_csat_cooldown("jane@example.com", "brand-1", NOW))
    assert result is False


# ── Atomic duplicate-send guard ──────────────────────────────────────────

def test_claim_succeeds_when_not_already_sent():
    with patch("src.channels.email_poller.supabase_update", return_value={"id": "ticket-1", "csat_sent": True}) as mock_update:
        result = run(EmailPoller._claim_csat_send("ticket-1", NOW))
    assert result is True
    args, _ = mock_update.call_args
    assert args[0] == "tickets"
    assert args[1] == {"id": "eq.ticket-1", "csat_sent": "is.false"}


def test_claim_fails_when_a_concurrent_call_already_claimed_it():
    # supabase_update returns {} when the conditional filter matched zero
    # rows - exactly what a lost race against a concurrent claim looks like.
    with patch("src.channels.email_poller.supabase_update", return_value={}):
        result = run(EmailPoller._claim_csat_send("ticket-1", NOW))
    assert result is False


def test_two_concurrent_claims_only_one_succeeds():
    """Simulates the actual race: two overlapping poller runs both try to
    claim the same ticket. Only the first (real) update should win."""
    call_count = {"n": 0}

    def fake_update(table, match, data):
        call_count["n"] += 1
        return {"id": "ticket-1"} if call_count["n"] == 1 else {}

    with patch("src.channels.email_poller.supabase_update", side_effect=fake_update):
        first = run(EmailPoller._claim_csat_send("ticket-1", NOW))
        second = run(EmailPoller._claim_csat_send("ticket-1", NOW))

    assert first is True
    assert second is False


# ── Email body sanity (no AI call, no fabricated data) ───────────────────

def test_build_csat_email_uses_only_passed_in_data():
    plain, html = EmailPoller._build_csat_email("ticket-42", "Acme Co")
    assert "ticket_id=ticket-42" in plain
    assert "Acme Co" in plain
    assert "ticket_id=ticket-42" in html
