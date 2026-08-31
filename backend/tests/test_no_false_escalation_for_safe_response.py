"""
Root cause of a confirmed-live contradiction: a ticket showed
"Escalated: Needs Your Attention" / "Read the conversation and reply
manually" in the merchant dashboard, while the SAME conversation's
Activity timeline ended with "Email sent" and the customer had actually
received Luna's response.

Traced to the ownership-mismatch (identity-verification) response path:
when Shopify finds the order but the conversation's verified email
doesn't match it, the tool_context instruction tells the model this is
"a statement, followed by escalation" so it states the situation plainly
instead of looping the customer through a clarifying question - but the
model reliably also sets escalate=True from that wording. Downstream
routing (message_processor.py's _decide_ticket_routing) then reads
escalate=True as "a human must act", landing status="escalated" even
though should_auto_reply=True (the SAME branch sends the email). The
reply was a complete, safe, self-contained response - Luna correctly
withheld protected order details and told the customer what's needed
next. Nothing was actually waiting on the merchant; the ball was in the
CUSTOMER's court to verify their identity.

_enforce_no_escalation_for_safe_identity_verification_response is the
code-level backstop (pure function, no LLM/mocking needed) plus one
end-to-end check through the real routing function proving the full
chain now lands on a consistent status.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.agent.customer_success_agent import (  # noqa: E402
    _enforce_no_escalation_for_safe_identity_verification_response,
)
from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402


_REAL_REPLY = (
    "Dear Bushra, I found order #1005, but the email you're contacting us from "
    "doesn't match the one on that order. I need our team to verify ownership "
    "before I can share details. If you meant a different order, let me know."
)


# ── Case F: safe identity-verification response — Backstop unit tests ──────

def test_safe_identity_verification_response_clears_escalate_and_status():
    structured = {
        "reply_body": _REAL_REPLY, "status": "escalated", "escalate": True,
        "risk_level": "low", "confidence_score": 75,
    }
    result = _enforce_no_escalation_for_safe_identity_verification_response(structured, True)
    assert result["escalate"] is False
    assert result["status"] == "auto_resolved"


def test_no_identity_verification_flag_leaves_structured_untouched():
    structured = {"reply_body": _REAL_REPLY, "status": "escalated", "escalate": True, "risk_level": "low"}
    result = _enforce_no_escalation_for_safe_identity_verification_response(structured, False)
    assert result["escalate"] is True
    assert result["status"] == "escalated"


def test_failed_generation_is_never_overridden_into_a_fake_handled_state():
    """No real reply exists - must not manufacture a 'handled' state."""
    structured = {"reply_body": "", "status": "escalated", "escalate": True, "risk_level": "low"}
    result = _enforce_no_escalation_for_safe_identity_verification_response(structured, True)
    assert result["escalate"] is True
    assert result["status"] == "escalated"


def test_independently_high_risk_is_never_weakened():
    """A genuine, separate high-risk signal must still escalate - this
    backstop only fixes the specific identity-verification contradiction,
    never generally lowers the security bar."""
    structured = {"reply_body": _REAL_REPLY, "status": "escalated", "escalate": True, "risk_level": "high"}
    result = _enforce_no_escalation_for_safe_identity_verification_response(structured, True)
    assert result["escalate"] is True
    assert result["status"] == "escalated"


def test_status_that_was_not_escalated_is_left_alone_only_escalate_flips():
    structured = {"reply_body": _REAL_REPLY, "status": "auto_resolved_review", "escalate": True, "risk_level": "low"}
    result = _enforce_no_escalation_for_safe_identity_verification_response(structured, True)
    assert result["escalate"] is False
    assert result["status"] == "auto_resolved_review"  # untouched — only "escalated" gets corrected


# ── End-to-end through the real routing function ────────────────────────

def test_fixed_agent_result_routes_to_a_consistent_sent_state_not_escalated():
    """Case A/F combined: proves the full chain (agent backstop -> routing)
    lands on ONE consistent outcome - sent AND not escalated - instead of
    the contradictory pair the live ticket showed."""
    structured = {
        "reply_body": _REAL_REPLY, "status": "escalated", "escalate": True,
        "risk_level": "low", "confidence_score": 75,
    }
    fixed = _enforce_no_escalation_for_safe_identity_verification_response(structured, True)

    proc = UnifiedMessageProcessor()
    routing = proc._decide_ticket_routing(
        ai_mode="active", is_overridden=False, confidence=0.75, confidence_threshold=0.65,
        ai_flagged_escalate=fixed["escalate"], risk_level=fixed["risk_level"], reply_body=fixed["reply_body"],
    )

    assert routing["should_auto_reply"] is True
    assert routing["status"] != "escalated"


def test_unfixed_contradiction_is_reproduced_without_the_backstop():
    """Sanity check that this is a real bug, not a hypothetical: the SAME
    routing call with the raw (unpatched) agent output — escalate=True but
    a reply that DOES get auto-sent — reproduces exactly the reported
    contradiction (should_auto_reply=True and status="escalated" at once)."""
    proc = UnifiedMessageProcessor()
    routing = proc._decide_ticket_routing(
        ai_mode="active", is_overridden=False, confidence=0.75, confidence_threshold=0.65,
        ai_flagged_escalate=True, risk_level="low", reply_body=_REAL_REPLY,
    )
    assert routing["should_auto_reply"] is True
    assert routing["status"] == "escalated"  # the exact contradiction, reproduced
