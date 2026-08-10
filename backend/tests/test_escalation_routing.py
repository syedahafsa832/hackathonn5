"""
Escalation flow: unsupported/ambiguous/failed/sensitive requests must route to
a human queue, and that routing must be visible to the merchant (ticket status
+ Badge label in the dashboard), not silently dropped.

_decide_ticket_routing in message_processor.py is the single place that turns
(confidence, risk_level, ai_mode, escalate flag) into a ticket status. It was
extracted from an inline if/elif chain in process_message() specifically so it
could be tested directly, without mocking Supabase/plan_service/the AI agent.
Behavior is unchanged from the inline version - these tests cover the routing
rules already in place, matching real dashboard status badges in Badge.jsx
(escalated, requires_human/human_managing, ai_suggested, auto_resolved_review).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402

processor = UnifiedMessageProcessor()


def _route(**kwargs):
    defaults = dict(
        ai_mode="active", is_overridden=False, confidence=0.9,
        confidence_threshold=0.65, ai_flagged_escalate=False,
        risk_level="low", reply_body="Here you go!",
    )
    defaults.update(kwargs)
    return processor._decide_ticket_routing(**defaults)


def test_high_confidence_low_risk_auto_resolves_without_escalation():
    result = _route(confidence=0.9, risk_level="low", ai_flagged_escalate=False)
    assert result["status"] == "auto_resolved"
    assert result["should_auto_reply"] is True


def test_high_risk_sensitive_operation_escalates_even_with_high_confidence():
    """Sensitive operations must escalate for approval regardless of how
    confident the model is - risk_level alone can force escalation."""
    result = _route(confidence=0.95, risk_level="high", ai_flagged_escalate=False)
    assert result["status"] == "escalated"


def test_ai_flagged_low_confidence_escalates():
    """Low-confidence responses must escalate rather than auto-resolve."""
    result = _route(confidence=0.3, risk_level="low", ai_flagged_escalate=True)
    assert result["status"] == "escalated"


def test_escalated_with_reply_still_sends_acknowledgment_not_silence():
    """An escalated conversation isn't silently dropped - if there's a safe
    acknowledgment reply and confidence is still reasonable, it gets sent
    while the sensitive action itself waits in the approval queue."""
    result = _route(confidence=0.6, risk_level="high", reply_body="We'll review this.")
    assert result["status"] == "escalated"
    assert result["should_auto_reply"] is True
    assert result["ai_reply"] == "We'll review this."


def test_escalated_with_no_safe_reply_stores_draft_and_does_not_send():
    """Very low confidence + high risk: nothing gets auto-sent, but the draft
    is preserved (not discarded) so a human can see what the AI would have said."""
    result = _route(confidence=0.2, risk_level="high", reply_body="Uncertain reply")
    assert result["status"] == "escalated"
    assert result["should_auto_reply"] is False
    assert result["ai_draft"] == "Uncertain reply"


def test_human_override_suppresses_ai_reply_but_keeps_draft_visible():
    """Merchant has taken over the thread - AI must not talk over them, but
    its draft is still stored so nothing is lost."""
    result = _route(is_overridden=True)
    assert result["status"] == "human_managing"
    assert result["should_auto_reply"] is False
    assert result["ai_draft"] == "Here you go!"


def test_paused_mode_stores_draft_for_review_never_auto_sends():
    result = _route(ai_mode="paused")
    assert result["status"] == "ai_suggested"
    assert result["should_auto_reply"] is False


def test_medium_confidence_low_risk_sends_but_flags_for_review():
    result = _route(confidence=0.55, risk_level="low", ai_flagged_escalate=False)
    assert result["status"] == "auto_resolved_review"
    assert result["should_auto_reply"] is True


def test_unrecognized_ai_mode_does_not_overwrite_existing_ticket_status():
    """An unrecognized ai_mode value must not silently null out the ticket's
    status - the caller only applies a status when one is actually returned."""
    result = _route(ai_mode="some_future_mode")
    assert result["status"] is None
    assert result["should_auto_reply"] is False
