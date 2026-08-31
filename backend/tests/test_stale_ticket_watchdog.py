"""
Ticket-level watchdog: find_and_recover_stale_tickets() and the
exhaustion-escalates-the-ticket fix in reschedule_or_exhaust().

Root cause this closes: reclaim_stale_processing() only recovers a retry
ROW whose worker died - it can't help a ticket that never made it into
ai_response_retries at all. Confirmed live: a process crash killed the
pipeline between the "preparing" event and customer_success_agent.py ever
raising a classifiable provider error, so no retry was ever queued and an
18-minute-old ticket sat at status='processing' with zero retry rows and
nothing watching it.

All Supabase calls mocked - no live services required.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services import provider_retry_service as prs  # noqa: E402


def _stale_ticket(**overrides):
    t = {"id": "ticket-1", "brand_id": "brand-1", "status": "processing", "updated_at": "2020-01-01T00:00:00+00:00"}
    t.update(overrides)
    return t


# ── find_and_recover_stale_tickets ──────────────────────────────────────

def test_stale_ticket_is_moved_to_ai_retry_pending_and_queued():
    ticket = _stale_ticket()
    with patch("src.services.provider_retry_service.supabase_select", return_value=[ticket]), \
         patch("src.services.provider_retry_service.supabase_update", return_value={"id": "ticket-1"}) as mock_update, \
         patch("src.services.provider_retry_service.enqueue_retry", return_value={"id": "row-1"}) as mock_enqueue:
        recovered = prs.find_and_recover_stale_tickets()

    assert recovered == 1
    # First call is the conditional ticket status flip.
    match, data = mock_update.call_args_list[0].args[1], mock_update.call_args_list[0].args[2]
    assert match == {"id": "eq.ticket-1", "status": "eq.processing"}
    assert data["status"] == "ai_retry_pending"
    mock_enqueue.assert_called_once_with(
        "ticket-1", "brand-1", attempts=[],
        last_error="ticket found stuck in processing past the timeout — worker likely crashed or was killed mid-generation",
    )


def test_stale_ticket_query_filters_by_status_and_age():
    with patch("src.services.provider_retry_service.supabase_select", return_value=[]) as mock_select:
        prs.find_and_recover_stale_tickets()
    params = mock_select.call_args.args[1]
    assert params["status"] == "eq.processing"
    assert params["updated_at"].startswith("lt.")


def test_recent_processing_ticket_is_never_touched():
    """The watchdog's own query already filters by age server-side (updated_at
    < cutoff) - this proves nothing here would independently re-touch a
    ticket genuinely still within its normal processing window."""
    with patch("src.services.provider_retry_service.supabase_select", return_value=[]), \
         patch("src.services.provider_retry_service.supabase_update") as mock_update:
        recovered = prs.find_and_recover_stale_tickets()
    assert recovered == 0
    mock_update.assert_not_called()


def test_concurrent_watchdog_lost_race_is_skipped_not_double_recovered():
    """Two workers racing on the same stale ticket: the conditional
    UPDATE ... WHERE status='processing' only lets one through. The loser
    must not also enqueue a retry."""
    ticket = _stale_ticket()
    with patch("src.services.provider_retry_service.supabase_select", return_value=[ticket]), \
         patch("src.services.provider_retry_service.supabase_update", return_value={}), \
         patch("src.services.provider_retry_service.enqueue_retry") as mock_enqueue:
        recovered = prs.find_and_recover_stale_tickets()

    assert recovered == 0
    mock_enqueue.assert_not_called()


def test_recovery_is_idempotent_when_a_retry_already_exists():
    """enqueue_retry's own unique-index/409 handling already makes a second
    enqueue for the same ticket a safe no-op - the watchdog must still
    report a normal outcome (ticket moved to ai_retry_pending) rather than
    treating "already recovering" as a failure."""
    ticket = _stale_ticket()
    with patch("src.services.provider_retry_service.supabase_select", return_value=[ticket]), \
         patch("src.services.provider_retry_service.supabase_update", return_value={"id": "ticket-1"}), \
         patch("src.services.provider_retry_service.enqueue_retry", return_value=None):  # already active
        recovered = prs.find_and_recover_stale_tickets()

    assert recovered == 1  # ticket was still moved out of 'processing'


# ── reschedule_or_exhaust now escalates the ticket on exhaustion ──────────

def test_exhaustion_escalates_the_ticket_visibly():
    row = {"id": "row-1", "ticket_id": "ticket-1", "brand_id": "brand-1", "retry_count": 5, "max_retries": 6}
    with patch("src.services.provider_retry_service.supabase_update", return_value={"id": "row-1"}) as mock_update, \
         patch("src.services.provider_retry_service.supabase_insert", return_value={}):
        outcome = prs.reschedule_or_exhaust(row, "still no provider available")

    assert outcome == "exhausted"
    ticket_updates = [c for c in mock_update.call_args_list if c.args[0] == "tickets"]
    assert len(ticket_updates) == 1
    match, data = ticket_updates[0].args[1], ticket_updates[0].args[2]
    assert match == {"id": "eq.ticket-1"}
    assert data["status"] == "escalated"
    assert data["escalation_reason"] == "AI processing failed after automatic retries. Human review required."


def test_reschedule_not_yet_exhausted_never_touches_the_ticket():
    """A transient failure that still has retries left must stay invisible
    to the merchant - only exhaustion becomes a real escalation."""
    row = {"id": "row-1", "ticket_id": "ticket-1", "brand_id": "brand-1", "retry_count": 1, "max_retries": 6, "outage_tier": "retryable"}
    with patch("src.services.provider_retry_service.supabase_update", return_value={"id": "row-1"}) as mock_update:
        outcome = prs.reschedule_or_exhaust(row, "still rate limited")

    assert outcome == "rescheduled"
    ticket_updates = [c for c in mock_update.call_args_list if c.args[0] == "tickets"]
    assert ticket_updates == []
