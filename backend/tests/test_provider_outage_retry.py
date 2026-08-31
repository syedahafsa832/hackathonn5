"""
Automatic resumption after temporary AI-provider quota recovery.

Root cause of the reported bug: when every configured AI provider failed
for one message (AllProvidersFailedError), customer_success_agent returned
a canned "escalated" result and message_processor.py permanently marked
the ticket "escalated" - the customer's message was abandoned with no way
to resume once a free-tier provider's quota reset, short of a merchant
noticing and replying manually.

Fix: provider_retry_service.py (a small DB-backed queue —
ai_response_retries, migrations/058) + provider_retry_worker.py (a polling
loop, same shape as retention_worker.py — no new dependency) +
UnifiedMessageProcessor.retry_pending_response() (reuses the existing
routing/decision/send methods, re-fetching the ticket and its current
latest customer message fresh before regenerating).

These tests are unit-level with every DB/AI/email call mocked — no live
Supabase/Mistral/Gmail traffic. See the final report for what could and
could not be verified end-to-end.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services import provider_retry_service as prs  # noqa: E402


# ── 1. Provider failure classification ──────────────────────────────────────

def test_rate_limit_and_quota_and_timeout_and_5xx_are_retryable():
    for reason in ("rate_limited", "quota_exceeded", "timeout", "provider_error_503"):
        assert prs.classify_outage([{"label": "primary", "reason": reason}]) == "retryable"


def test_unclassified_reason_is_fast_fail():
    """An invalid key/model surfaces through ai_provider_manager._describe()
    as its generic 'temporary_failure' bucket (confirmed live this session:
    a 403 'model not available in your subscription tier' doesn't match
    429/quota/timeout/5xx text) - treated as fast_fail here so a genuine
    misconfiguration still reaches a human quickly, not after hours of
    silent bounded retries."""
    assert prs.classify_outage([{"label": "primary", "reason": "temporary_failure"}]) == "fast_fail"


def test_any_retryable_attempt_among_several_wins_optimistically():
    attempts = [{"label": "primary", "reason": "temporary_failure"}, {"label": "fallback_1", "reason": "rate_limited"}]
    assert prs.classify_outage(attempts) == "retryable"


def test_empty_attempts_is_fast_fail_not_a_crash():
    assert prs.classify_outage([]) == "fast_fail"


# ── 2. Queue mechanics: enqueue / claim / reschedule / exhaust ─────────────

def test_enqueue_retry_schedules_full_window_for_retryable_tier():
    with patch("src.services.provider_retry_service.supabase_insert",
               return_value={"id": "row-1"}) as mock_insert:
        row = prs.enqueue_retry("ticket-1", "brand-1", [{"label": "p", "reason": "rate_limited"}], "429")
    assert row == {"id": "row-1"}
    kwargs = mock_insert.call_args.args[1]
    assert kwargs["outage_tier"] == "retryable"
    assert kwargs["max_retries"] == len(prs.RETRYABLE_SCHEDULE_MINUTES)


def test_enqueue_retry_schedules_short_window_for_fast_fail_tier():
    with patch("src.services.provider_retry_service.supabase_insert",
               return_value={"id": "row-1"}) as mock_insert:
        prs.enqueue_retry("ticket-1", "brand-1", [{"label": "p", "reason": "temporary_failure"}], "403")
    kwargs = mock_insert.call_args.args[1]
    assert kwargs["outage_tier"] == "fast_fail"
    assert kwargs["max_retries"] == len(prs.FAST_FAIL_SCHEDULE_MINUTES)


def test_enqueue_does_not_duplicate_an_already_active_retry():
    """A second outage on the same ticket before the first retry resolves
    must not queue a second job — the unique partial index on
    (ticket_id) WHERE status IN (pending, processing) causes a 409, which
    enqueue_retry treats as 'already queued', not an error."""
    with patch("src.services.provider_retry_service.supabase_insert",
               side_effect=Exception("409 Conflict")):
        row = prs.enqueue_retry("ticket-1", "brand-1", [], "err")
    assert row is None


def test_claim_due_retries_skips_a_row_lost_to_a_concurrent_claim():
    due_rows = [{"id": "row-1", "status": "pending"}, {"id": "row-2", "status": "pending"}]
    # row-1's conditional UPDATE (WHERE status=pending) matches 0 rows —
    # another worker/process claimed it first.
    with patch("src.services.provider_retry_service.supabase_select", return_value=due_rows), \
         patch("src.services.provider_retry_service.supabase_update",
               side_effect=[{}, {"id": "row-2", "status": "processing"}]) as mock_update:
        claimed = prs.claim_due_retries()
    assert [r["id"] for r in claimed] == ["row-2"]
    assert mock_update.call_count == 2


def test_reclaim_stale_processing_puts_orphaned_rows_back_to_pending():
    """Regression for a real live incident: a Render deploy rolled the
    container over 4 seconds after a worker claimed a job, permanently
    stranding it at status='processing' — claim_due_retries only ever
    looks at status='pending', so nothing would have picked it up again
    without this."""
    stale_row = {"id": "row-1", "status": "processing", "updated_at": "2020-01-01T00:00:00+00:00"}
    with patch("src.services.provider_retry_service.supabase_select", return_value=[stale_row]) as mock_select, \
         patch("src.services.provider_retry_service.supabase_update", return_value={"id": "row-1"}) as mock_update:
        reclaimed = prs.reclaim_stale_processing()

    assert reclaimed == 1
    assert mock_select.call_args.args[1]["status"] == "eq.processing"
    match, data = mock_update.call_args.args[1], mock_update.call_args.args[2]
    assert match == {"id": "eq.row-1", "status": "eq.processing"}
    assert data["status"] == "pending"


def test_reclaim_stale_processing_skips_a_row_finished_between_select_and_update():
    """A worker that's actually still alive and finishes the job in the
    gap between the select and the conditional update must not be
    overridden — the WHERE status='processing' guard matches 0 rows and
    is silently skipped, same pattern as claim_due_retries' own race
    guard."""
    stale_row = {"id": "row-1", "status": "processing", "updated_at": "2020-01-01T00:00:00+00:00"}
    with patch("src.services.provider_retry_service.supabase_select", return_value=[stale_row]), \
         patch("src.services.provider_retry_service.supabase_update", return_value={}):
        reclaimed = prs.reclaim_stale_processing()

    assert reclaimed == 0


def test_reschedule_increments_count_and_extends_delay():
    row = {"id": "row-1", "ticket_id": "t-1", "retry_count": 1, "max_retries": 6, "outage_tier": "retryable"}
    with patch("src.services.provider_retry_service.supabase_update") as mock_update:
        outcome = prs.reschedule_or_exhaust(row, "still rate limited")
    assert outcome == "rescheduled"
    data = mock_update.call_args.args[2]
    assert data["status"] == "pending"
    assert data["retry_count"] == 2


def test_exhausts_after_max_retries_never_retries_indefinitely():
    row = {"id": "row-1", "ticket_id": "t-1", "retry_count": 5, "max_retries": 6, "outage_tier": "fast_fail"}
    with patch("src.services.provider_retry_service.supabase_update") as mock_update:
        outcome = prs.reschedule_or_exhaust(row, "invalid api key")
    assert outcome == "exhausted"
    data = mock_update.call_args.args[2]
    assert data["status"] == "exhausted"


# ── 3. already_responded — reuses existing idempotency fields ─────────────

@pytest.mark.parametrize("ticket", [
    {"human_approved": True}, {"response_sent": True}, {"email_sent": True},
])
def test_already_responded_true_for_existing_idempotency_fields(ticket):
    assert prs.already_responded(ticket) is True


def test_already_responded_false_for_a_genuinely_untouched_ticket():
    assert prs.already_responded({"status": "ai_retry_pending"}) is False


# ── 4. message_processor.retry_pending_response — stop conditions ─────────

from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402


def _proc():
    return UnifiedMessageProcessor()


@pytest.mark.asyncio
async def test_retry_cancelled_when_ticket_already_responded():
    ticket = {"id": "t-1", "brand_id": "b-1", "human_approved": True, "customer_email": "c@example.com"}
    with patch("src.workers.message_processor.supabase_select", return_value=[ticket]), \
         patch("src.services.provider_retry_service.mark_cancelled") as mock_cancel:
        result = await _proc().retry_pending_response({"id": "row-1", "ticket_id": "t-1", "retry_count": 0})
    assert result["outcome"] == "cancelled"
    assert result["reason"] == "already_responded"
    mock_cancel.assert_called_once()


@pytest.mark.asyncio
async def test_retry_cancelled_on_human_takeover():
    """A merchant manually taking over a conversation (conversation_overrides,
    the existing takeover mechanism) must suppress the delayed AI response —
    checked fresh at retry time, not assumed from the original attempt."""
    ticket = {"id": "t-1", "brand_id": "b-1", "customer_email": "c@example.com"}
    with patch("src.workers.message_processor.supabase_select", return_value=[ticket]), \
         patch("src.services.supabase_service.supabase_service.check_conversation_override",
               new=AsyncMock(return_value=True)), \
         patch("src.services.provider_retry_service.mark_cancelled") as mock_cancel:
        result = await _proc().retry_pending_response({"id": "row-1", "ticket_id": "t-1", "retry_count": 0})
    assert result["outcome"] == "cancelled"
    assert result["reason"] == "human_takeover"
    mock_cancel.assert_called_once()


@pytest.mark.asyncio
async def test_retry_cancelled_when_ticket_no_longer_exists():
    with patch("src.workers.message_processor.supabase_select", return_value=[]), \
         patch("src.services.provider_retry_service.mark_cancelled") as mock_cancel:
        result = await _proc().retry_pending_response({"id": "row-1", "ticket_id": "gone", "retry_count": 0})
    assert result["outcome"] == "cancelled"
    assert result["reason"] == "ticket_no_longer_exists"
    mock_cancel.assert_called_once()


# ── 5. retry_pending_response — regeneration and multi-turn safety ────────

def _base_ticket(**overrides):
    ticket = {
        "id": "t-1", "brand_id": "b-1", "customer_email": "c@example.com",
        "customer_name": "Jane", "subject": "Where is my order?", "channel": "email",
        "messages": [
            {"direction": "inbound", "body": "Where is my order #1009?"},
        ],
        "message": "Where is my order #1009?",
    }
    ticket.update(overrides)
    return ticket


def _patched(ticket, ai_result, **extra_patches):
    patches = {
        "src.workers.message_processor.supabase_select": MagicMock(return_value=[ticket]),
        "src.workers.message_processor.supabase_update": MagicMock(return_value={}),
        "src.workers.message_processor.supabase_insert": MagicMock(return_value={}),
        "src.services.supabase_service.supabase_service.check_conversation_override": AsyncMock(return_value=False),
        "src.services.supabase_service.supabase_service.get_system_settings": AsyncMock(
            return_value={"ai_mode": "active", "confidence_threshold": 0.65, "auto_reply_enabled": True}),
        "src.services.supabase_service.supabase_service.get_or_create_customer": AsyncMock(
            return_value={"id": "cust-1", "email": "c@example.com"}),
        "src.workers.message_processor.UnifiedMessageProcessor._build_customer_history": AsyncMock(return_value=None),
        "src.workers.message_processor.UnifiedMessageProcessor._check_thread_override": AsyncMock(return_value=False),
        "src.workers.message_processor.customer_success_agent.generate_channel_appropriate_response": AsyncMock(return_value=ai_result),
        "src.workers.message_processor.brand_message_processor._log_conversation": AsyncMock(),
        "src.workers.message_processor.UnifiedMessageProcessor._send_email_with_logging": AsyncMock(),
        "src.services.provider_retry_service.mark_succeeded": MagicMock(),
        "src.services.provider_retry_service.mark_cancelled": MagicMock(),
    }
    patches.update(extra_patches)
    return [patch(target, new=val) for target, val in patches.items()]


@pytest.mark.asyncio
async def test_retry_regenerates_from_the_latest_message_not_the_stale_original():
    """Multi-turn safety: if the customer sent a newer message ("hello?")
    after the original failed attempt but before the retry runs, the retry
    must answer the CURRENT latest message, not resend a stale response to
    the original one."""
    ticket = _base_ticket(messages=[
        {"direction": "inbound", "body": "Where is my order #1009?"},
        {"direction": "inbound", "body": "hello?"},
    ])
    ai_result = {
        "reply_body": "Hi! Let me check on that.", "ai_reply_generated": True,
        "confidence_score": 90, "intent": "general_inquiry", "risk_level": "low",
        "escalate": False, "sentiment": "neutral",
    }
    agent_mock = AsyncMock(return_value=ai_result)
    ps = [p for p in _patched(ticket, ai_result,
          **{"src.workers.message_processor.customer_success_agent.generate_channel_appropriate_response": agent_mock})]
    for p in ps:
        p.start()
    try:
        result = await _proc().retry_pending_response({"id": "row-1", "ticket_id": "t-1", "retry_count": 0})
    finally:
        for p in ps:
            p.stop()

    assert result["outcome"] == "sent"
    _, kwargs = agent_mock.call_args
    assert kwargs["query"] == "hello?"


@pytest.mark.asyncio
async def test_retry_still_provider_outage_reports_retryable_failure_no_send():
    """Provider still unavailable on this attempt — must not send anything,
    must not mark the queue row succeeded; the caller (worker) reschedules."""
    ticket = _base_ticket()
    ai_result = {"provider_outage": True, "escalation_reason": "still out of quota", "reply_body": ""}
    send_mock = AsyncMock()
    ps = _patched(ticket, ai_result,
                  **{"src.workers.message_processor.UnifiedMessageProcessor._send_email_with_logging": send_mock})
    for p in ps:
        p.start()
    try:
        result = await _proc().retry_pending_response({"id": "row-1", "ticket_id": "t-1", "retry_count": 1})
    finally:
        for p in ps:
            p.stop()

    assert result["outcome"] == "retryable_failure"
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_success_sends_exactly_once_and_marks_queue_row_succeeded():
    ticket = _base_ticket()
    ai_result = {
        "reply_body": "Your order shipped yesterday!", "ai_reply_generated": True,
        "confidence_score": 92, "intent": "order_status", "risk_level": "low",
        "escalate": False, "sentiment": "neutral",
    }
    send_mock = AsyncMock()
    mark_succeeded_mock = MagicMock()
    ps = _patched(ticket, ai_result,
                  **{"src.workers.message_processor.UnifiedMessageProcessor._send_email_with_logging": send_mock,
                     "src.services.provider_retry_service.mark_succeeded": mark_succeeded_mock})
    for p in ps:
        p.start()
    try:
        result = await _proc().retry_pending_response({"id": "row-1", "ticket_id": "t-1", "retry_count": 0})
    finally:
        for p in ps:
            p.stop()

    assert result["outcome"] == "sent"
    send_mock.assert_awaited_once()
    mark_succeeded_mock.assert_called_once_with("row-1")


@pytest.mark.asyncio
async def test_retry_aborts_send_if_human_replied_during_regeneration():
    """A human could reply in the seconds it takes to regenerate — the
    fresh re-check right before sending must catch that and skip the send,
    not just the check at the very start of the function."""
    ticket = _base_ticket()
    already_answered_ticket = _base_ticket(response_sent=True)
    ai_result = {
        "reply_body": "Your order shipped yesterday!", "ai_reply_generated": True,
        "confidence_score": 92, "intent": "order_status", "risk_level": "low",
        "escalate": False, "sentiment": "neutral",
    }
    send_mock = AsyncMock()
    # 1) ticket_rows (top of function), 2) brand_rows (tenant lookup),
    # 3) fresh_rows (the idempotency re-check right before sending).
    select_mock = MagicMock(side_effect=[[ticket], [{"id": "b-1", "tenant_id": None}], [already_answered_ticket]])
    ps = _patched(ticket, ai_result,
                  **{"src.workers.message_processor.supabase_select": select_mock,
                     "src.workers.message_processor.UnifiedMessageProcessor._send_email_with_logging": send_mock})
    for p in ps:
        p.start()
    try:
        result = await _proc().retry_pending_response({"id": "row-1", "ticket_id": "t-1", "retry_count": 0})
    finally:
        for p in ps:
            p.stop()

    assert result["outcome"] == "cancelled"
    assert result["reason"] == "already_responded_during_retry"
    send_mock.assert_not_awaited()
