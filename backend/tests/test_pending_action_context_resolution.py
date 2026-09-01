"""
Multi-turn context loss, root cause and fix.

THE BUG: Turn 1 "Can I cancel order #1009?" stages a cancellation request
(a real actions-table row, status='pending'). Turn 2 "yes, please go
ahead" carries no order number and no action verb of its own -
intent_detector.detect() only ever sees this one message, no conversation
history, so in isolation it correctly classifies it as action_type="none".
Because customer_success_agent.py only calls return_actions_integration's
handle_return_intent() when has_action is True, the entire action layer
was skipped on turn 2 and the agent fell back to a generic response that
claimed it couldn't find the order - even though the order and the
pending action both durably existed.

THE FIX IS NOT A PHRASE LIST. It is a two-part, genuinely context-aware
mechanism:

1. A fully deterministic, durable-state check:
   ReturnActionsIntegration.find_pending_actions_for_ticket(ticket_id)
   queries the real `actions` table (no new memory system) for this
   ticket's active (pending/approved/executed/awaiting_manual_step) rows.
   This is a fact, never a guess - and if MORE than one exists (e.g. two
   different orders each have an open action on the same ticket), the
   caller treats that as genuine ambiguity and does NOT auto-resolve,
   satisfying the "never just use the last order mentioned" requirement.

2. When exactly one pending action exists, its summary (action_type,
   order_number, status) is injected into intent_detector's OWN prompt as
   PENDING ACTION CONTEXT, and the model - which already does real
   language understanding for every fresh message - is asked to judge
   whether the CURRENT message confirms it, explicitly redirects to a
   different order, or is unrelated. This handles "yes", "absolutely",
   "that's fine", "cancel it", and any other real phrasing the same way,
   because it is not pattern-matched at all; it is interpreted.

A small, explicitly-labeled keyword fallback exists ONLY for the window
where no AI provider is reachable at all (_keyword_fallback /
_looks_like_bare_agreement_fallback) - the same degrade-path philosophy
already used everywhere else in this file (_CANCEL_FRAGS,
_RETURN_FRAGS, etc. are the same kind of fallback for a FRESH message
when the LLM is down). This is not the primary mechanism and is tested
separately, clearly labeled as the fallback it is.

handle_return_intent()'s own eligibility/approval/duplicate-action
pipeline is completely unchanged - this only ever affects which
action_type/order_id reach it, never whether the request is authorized.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.intent_detector import (  # noqa: E402
    IntentDetector, IntentResult, NO_ACTION,
    _keyword_fallback, _looks_like_bare_agreement_fallback, _PENDING_CONTEXT_TEMPLATE,
)


# ── 1. Durable state: find_pending_actions_for_ticket ──────────────────────

@pytest.mark.asyncio
async def test_finds_the_one_active_action_for_a_ticket():
    integration = ReturnActionsIntegration()
    row = {"action_type": "cancel", "order_number": "1009", "status": "pending"}
    with patch("src.services.return_actions_integration.supabase_select", return_value=[row]):
        result = await integration.find_pending_actions_for_ticket("ticket-1")
    assert result == [row]


@pytest.mark.asyncio
async def test_no_ticket_id_returns_empty_without_a_query():
    integration = ReturnActionsIntegration()
    with patch("src.services.return_actions_integration.supabase_select") as mock_select:
        result = await integration.find_pending_actions_for_ticket(None)
    assert result == []
    mock_select.assert_not_called()


@pytest.mark.asyncio
async def test_db_failure_fails_open_to_empty_not_an_exception():
    integration = ReturnActionsIntegration()
    with patch("src.services.return_actions_integration.supabase_select", side_effect=RuntimeError("db down")):
        result = await integration.find_pending_actions_for_ticket("ticket-1")
    assert result == []


@pytest.mark.asyncio
async def test_multiple_orders_returns_every_match_ambiguity_is_the_callers_job():
    """The function itself never picks a "most recent" one - it surfaces
    the full set so the caller (customer_success_agent.py) can refuse to
    guess when there's more than one."""
    integration = ReturnActionsIntegration()
    rows = [
        {"action_type": "cancel", "order_number": "1009", "status": "pending"},
        {"action_type": "refund", "order_number": "1005", "status": "pending"},
    ]
    with patch("src.services.return_actions_integration.supabase_select", return_value=rows):
        result = await integration.find_pending_actions_for_ticket("ticket-1")
    assert len(result) == 2


# ── 2. Prompt construction actually carries the durable context ──────────

def test_pending_context_template_names_the_real_action_and_order():
    block = _PENDING_CONTEXT_TEMPLATE.format(action_type="cancel", order_number="1009", status="pending")
    assert "cancel" in block
    assert "1009" in block
    assert "DIFFERENT order number" in block  # the switch-safety instruction is present


@pytest.mark.asyncio
async def test_detect_includes_pending_context_in_the_actual_llm_prompt():
    """Proves the plumbing, not model judgment: when pending_action_context
    is passed, the real prompt sent to the provider contains the pending
    order/action - this is what lets the model reason about arbitrary
    phrasing instead of a fixed list."""
    detector = IntentDetector()
    captured = {}

    class _FakeMessage:
        content = '{"action_type": "cancel", "order_id": "1009", "confidence": 0.9}'

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = None

    def fake_create(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return _FakeResponse()

    fake_client = type("FakeClient", (), {
        "chat": type("Chat", (), {"completions": type("Completions", (), {"create": staticmethod(fake_create)})()})(),
    })()

    with patch.object(detector, "_get_client", return_value=fake_client):
        result = await detector.detect(
            "yes, please go ahead",
            pending_action_context={"action_type": "cancel", "order_number": "1009", "status": "pending"},
        )

    assert "PENDING ACTION CONTEXT" in captured["prompt"]
    assert "1009" in captured["prompt"]
    assert result.action_type == "cancel"
    assert result.order_id == "1009"


@pytest.mark.asyncio
async def test_detect_with_no_pending_context_sends_the_unmodified_prompt():
    """A ticket with no active action must not get a pending-context block
    injected — the model classifies the message on its own, exactly as
    for any brand-new conversation."""
    detector = IntentDetector()
    captured = {}

    class _FakeMessage:
        content = '{"action_type": "none", "confidence": 0.9}'

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = None

    def fake_create(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return _FakeResponse()

    fake_client = type("FakeClient", (), {
        "chat": type("Chat", (), {"completions": type("Completions", (), {"create": staticmethod(fake_create)})()})(),
    })()

    with patch.object(detector, "_get_client", return_value=fake_client):
        result = await detector.detect("yes")

    assert "PENDING ACTION CONTEXT" not in captured["prompt"]
    assert result.action_type == "none"


# ── 3. Keyword fallback — explicitly the LLM-unavailable degrade path ─────

def test_bare_agreement_fallback_matches_common_real_world_phrasings():
    for text in ["yes", "yes please", "sure", "go ahead", "please go ahead",
                 "do it", "please do it", "absolutely", "that's fine"]:
        assert _looks_like_bare_agreement_fallback(text), f"expected {text!r} to match"


def test_bare_agreement_fallback_never_matches_a_message_with_its_own_order_or_verb():
    """Security boundary even in the degrade path: a message naming a
    different order, or already using a real action verb, must be left to
    the normal keyword-fragment classification instead."""
    assert not _looks_like_bare_agreement_fallback("actually cancel 1005 instead")
    assert not _looks_like_bare_agreement_fallback("cancel it")
    assert not _looks_like_bare_agreement_fallback("by the way, what are your shipping times?")


def test_keyword_fallback_resolves_bare_agreement_against_pending_context():
    result = _keyword_fallback("yes, please go ahead", {"action_type": "cancel", "order_number": "1009", "status": "pending"})
    assert result.action_type == "cancel"
    assert result.order_id == "1009"
    assert result.source == "fallback_pending_confirmation"


def test_keyword_fallback_ignores_pending_context_without_a_bare_agreement():
    """An unrelated message must not be forced into the pending action even
    in the fallback path."""
    result = _keyword_fallback("what are your shipping times?", {"action_type": "cancel", "order_number": "1009", "status": "pending"})
    assert result.action_type == "none"


def test_keyword_fallback_with_no_pending_context_is_unaffected():
    result = _keyword_fallback("cancel my order #1234", None)
    assert result.action_type == "cancel"
    assert result.order_id == "1234"


def test_ambiguous_yes_with_no_pending_context_asks_for_clarification():
    """The exact negative case: 'yes' with nothing pending must not
    invent an order or action."""
    result = _keyword_fallback("yes", None)
    assert result.action_type == "none"
    assert not result.has_action
