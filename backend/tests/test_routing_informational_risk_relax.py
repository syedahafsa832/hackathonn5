"""
"what does your brand sell?" (confidence=80%, escalate=false,
risk_level="medium", intent="general_inquiry") was escalated with the
dashboard's generic "not confident enough" fallback text - the model was
NOT actually unconfident (80%) and never asked to escalate; risk_level
alone (neither of _decide_ticket_routing's two auto-resolve branches
accepts anything but risk_level="low") forced "escalated" regardless of
confidence.

Root cause: a brand/product/support question can land on risk_level=
"medium" purely from the model's own caution around brand/product claims,
even when confident and not flagged to escalate. Fix: relax "medium" to
"low" (never "high") for a fixed, non-action intent whitelist, only when
nothing else already flagged a real concern (ai_flagged_escalate,
identity_mismatch) - every existing escalation rule for actions/high-risk/
human-requests/unclassified intent is unchanged.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402
from src.agent.customer_success_agent import _validate_ai_json_reply  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

processor = UnifiedMessageProcessor()


def _route(**kwargs):
    defaults = dict(
        ai_mode="active", is_overridden=False, confidence=0.9,
        confidence_threshold=0.65, ai_flagged_escalate=False,
        risk_level="low", reply_body="Here you go!", intent="general_inquiry",
    )
    defaults.update(kwargs)
    return processor._decide_ticket_routing(**defaults)


# ── 1. Ordinary informational question, moderate/high confidence, medium
#      risk -> auto-resolves instead of escalating ──────────────────────

def test_brand_question_confident_medium_risk_auto_resolves():
    """The exact reported ticket shape."""
    result = _route(confidence=0.80, risk_level="medium", intent="general_inquiry",
                     ai_flagged_escalate=False, reply_body="We sell dresses, tops, and accessories!")
    assert result["status"] == "auto_resolved"
    assert result["should_auto_reply"] is True


def test_product_inquiry_moderate_confidence_medium_risk_still_answers():
    """Moderate (not high) confidence + medium risk on a safe intent still
    gets an AI response (auto_resolved_review), not automatic escalation."""
    result = _route(confidence=0.55, risk_level="medium", intent="product_inquiry",
                     ai_flagged_escalate=False, reply_body="Yes, we carry maxi dresses!")
    assert result["status"] == "auto_resolved_review"
    assert result["should_auto_reply"] is True


# ── 2. Explicit human request still escalates ────────────────────────────

def test_explicit_human_request_still_escalates():
    """_enforce_human_handoff_request already sets ai_flagged_escalate=True
    before routing ever runs - that alone blocks the new carve-out."""
    result = _route(confidence=0.9, risk_level="medium", intent="general_inquiry",
                     ai_flagged_escalate=True)
    assert result["status"] == "escalated"


# ── 3. High-risk / action cases unchanged ────────────────────────────────

def test_high_risk_still_escalates_regardless_of_intent():
    result = _route(confidence=0.95, risk_level="high", intent="general_inquiry",
                     ai_flagged_escalate=False)
    assert result["status"] == "escalated"


def test_refund_request_medium_risk_still_escalates():
    """An action intent (not on the safe-informational whitelist) keeps
    escalating on risk_level exactly as before - never weakened."""
    result = _route(confidence=0.9, risk_level="medium", intent="refund_request",
                     ai_flagged_escalate=False)
    assert result["status"] == "escalated"


# ── 4. Genuinely unclassified/uncertain intent keeps existing safe behavior ──

def test_unknown_intent_medium_risk_still_escalates():
    """Intent classification itself uncertain ("unknown", or None) is not
    on the whitelist - existing escalation behavior is preserved rather
    than guessing it's safe."""
    result = _route(confidence=0.9, risk_level="medium", intent="unknown",
                     ai_flagged_escalate=False)
    assert result["status"] == "escalated"

    result_none = _route(confidence=0.9, risk_level="medium", intent=None,
                          ai_flagged_escalate=False)
    assert result_none["status"] == "escalated"


def test_identity_mismatch_blocks_the_new_carveout_too():
    result = _route(confidence=0.9, risk_level="medium", intent="general_inquiry",
                     ai_flagged_escalate=False, identity_mismatch=True, reply_body="")
    assert result["status"] == "escalated"


# ── 5. Empty reply_body validator (from the prior fix) is untouched ─────

def test_empty_reply_body_validator_unchanged():
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content='{"reply_body": ""}'))]
    assert _validate_ai_json_reply(resp) == "empty_reply_body"

    resp2 = MagicMock()
    resp2.choices = [MagicMock(message=MagicMock(content='{"reply_body": "We sell dresses!"}'))]
    assert _validate_ai_json_reply(resp2) is None
