"""
P0 fix: chat-widget action requests must go through the same real staging
path Gmail already used - not be skipped because _is_chat was True.

Before this fix, customer_success_agent.process_customer_query wrapped the
whole intent-detection + return_actions.handle_return_intent call in
`if not _is_chat:`. Every chat-widget refund/cancellation/address-change
request therefore never ran eligibility checks and never inserted a row into
the actions table - the customer was still told "sent to our team for
review" based purely on the main LLM call's own self-reported "intent"/
"status" JSON fields, which v2_chat_widget.py used to build the
action_result the widget UI shows.

These tests cover the two things that actually changed:
1. process_customer_query now calls intent_detector.detect() and
   return_actions.handle_return_intent() unconditionally (chat included),
   and exposes the real outcome as structured["action_taken"] - sourced
   from the actual staging call, not the LLM's own claims.
2. v2_chat_widget.py's _map_action_result only reports a staged action to
   the customer when action_taken confirms a real row was created.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402
from src.api.routes.v2_chat_widget import _map_action_result  # noqa: E402


def run(coro):
    # asyncio.get_event_loop() returns the thread's previously-set loop even
    # if it's closed - which happens here in full-suite runs after an
    # earlier @pytest.mark.asyncio test (pytest-asyncio closes its own loop
    # per test). A closed loop raises "Event loop is closed" on
    # run_until_complete, which is what made this file's tests fail only in
    # the full suite, never in isolation - not a logic bug, just this
    # helper trusting a loop it never checked the state of.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _chat_query(message: str) -> str:
    # No "[CHAT MODE ...]" text prefix - chat formatting is now driven by
    # customer_info["channel"]="chat" alone (see the leak this fixed:
    # that prefix used to persist verbatim into actions.original_message).
    return f"Customer: {message}"


def _fake_ai_response(reply_body: str, intent: str = "cancellation_request"):
    import json
    content = json.dumps({"intent": intent, "reply_body": reply_body, "risk_level": "low"})
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _run_chat(message: str, intent_result: IntentResult, handle_return_intent_result: dict, reply_body: str = "I've sent your request to our team for review."):
    """Runs process_customer_query in chat mode with the AI call, brand
    lookup, and order lookup mocked out - only the staging path (the thing
    this fix changed) and its wiring into the response are real."""
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(return_value=(_fake_ai_response(reply_body), "test_provider", "test_model", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}))), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value={"success": False})), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=intent_result)) as mock_detect, \
         patch("src.agent.customer_success_agent.return_actions.handle_return_intent", new=AsyncMock(return_value=handle_return_intent_result)) as mock_handle:
        result = run(customer_success_agent.process_customer_query(
            query=_chat_query(message),
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1",
            store_id=None,
            ticket_id="ticket-1",
        ))
    return result, mock_detect, mock_handle


# ── The core regression: staging actually runs in chat mode ─────────────────

def test_chat_cancellation_request_calls_real_staging_path():
    intent_result = IntentResult(action_type="cancel", order_id="1011", raw_address=None, confidence=0.9, source="llm")
    staged = {"action_context": "**ACTION STAGED FOR APPROVAL**: cancellation submitted for review.", "staged": {"success": True, "action_id": "cancel-action-1"}}
    result, mock_detect, mock_handle = _run_chat("cancel my order #1011", intent_result, staged)

    mock_detect.assert_called_once()
    mock_handle.assert_called_once()
    assert result["action_taken"] == {"success": True, "action_id": "cancel-action-1"}


def test_chat_refund_request_calls_real_staging_path():
    intent_result = IntentResult(action_type="refund", order_id="1011", raw_address=None, confidence=0.9, source="llm")
    staged = {"action_context": "**ACTION STAGED FOR APPROVAL**: refund submitted for review.", "staged": {"success": True, "action_id": "refund-action-1"}}
    result, mock_detect, mock_handle = _run_chat("I want a refund for order #1011", intent_result, staged, reply_body="I've sent your refund request to our team.")

    mock_detect.assert_called_once()
    mock_handle.assert_called_once()
    assert result["action_taken"] == {"success": True, "action_id": "refund-action-1"}


def test_chat_address_change_request_calls_real_staging_path():
    intent_result = IntentResult(action_type="address_change", order_id="1011", raw_address="123 Main St, Springfield, IL", confidence=0.9, source="llm")
    staged = {"action_context": "**ACTION STAGED FOR APPROVAL**: address change submitted for review.", "staged": {"success": True, "action_id": "addr-action-1"}}
    result, mock_detect, mock_handle = _run_chat("please change my address on order #1011 to 123 Main St, Springfield, IL", intent_result, staged, reply_body="I've sent your address change request to our team.")

    mock_detect.assert_called_once()
    mock_handle.assert_called_once()
    assert result["action_taken"] == {"success": True, "action_id": "addr-action-1"}


# ── Normal chat/RAG behavior is unaffected ───────────────────────────────────

def test_chat_normal_inquiry_does_not_fabricate_an_action_result():
    intent_result = IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm")
    result, mock_detect, mock_handle = _run_chat(
        "what are your store hours?",
        intent_result,
        handle_return_intent_result={},  # should never be consulted - has_action is False
        reply_body="We're open 9-5 Monday to Friday!",
    )

    mock_detect.assert_called_once()
    mock_handle.assert_not_called()
    assert result["action_taken"] is None


# ── Failed/ambiguous action requests still fail safely ───────────────────────

def test_chat_ineligible_action_does_not_report_a_staged_action():
    """handle_return_intent ran (real eligibility check happened) but found
    the order isn't eligible - no 'staged' key is set. The customer must not
    be told anything was staged."""
    intent_result = IntentResult(action_type="cancel", order_id="1011", raw_address=None, confidence=0.9, source="llm")
    not_eligible = {"action_context": "NOT ELIGIBLE: order already fulfilled. Tell the customer a refund isn't possible for a shipped order."}
    result, mock_detect, mock_handle = _run_chat("cancel my order #1011", intent_result, not_eligible, reply_body="Unfortunately this order has already shipped, so it can't be cancelled.")

    mock_handle.assert_called_once()
    assert result["action_taken"] is None


def test_chat_ambiguous_address_does_not_report_a_staged_action():
    intent_result = IntentResult(action_type="address_change", order_id="1011", raw_address="somewhere else", confidence=0.5, source="llm")
    ambiguous = {"action_context": "ADDRESS TOO VAGUE — DO NOT CONFIRM. Ask the customer for a full street address, city, and country."}
    result, mock_detect, mock_handle = _run_chat("change my address to somewhere else", intent_result, ambiguous, reply_body="Could you share your full new address, including city and country?")

    mock_handle.assert_called_once()
    assert result["action_taken"] is None


# ── v2_chat_widget._map_action_result is grounded in the real outcome ───────

def test_map_action_result_reports_staged_only_when_action_taken_succeeded():
    order_data = {"orderNumber": "1011"}
    assert _map_action_result("cancellation_request", {"success": True, "action_id": "x"}, order_data) == {"type": "cancel_staged", "order_number": "1011", "action_id": "x"}


def test_map_action_result_returns_none_when_action_taken_is_missing():
    order_data = {"orderNumber": "1011"}
    assert _map_action_result("cancellation_request", None, order_data) is None


def test_map_action_result_returns_none_when_staging_failed():
    order_data = {"orderNumber": "1011"}
    assert _map_action_result("refund_request", {"success": False, "error": "db error"}, order_data) is None


def test_map_action_result_ignores_llm_self_reported_intent_without_real_staging():
    """Regression for the exact bug found live: the LLM can self-report
    'cancellation_request' with no real action ever having been created -
    action_taken (not the intent string alone) must gate the response."""
    order_data = {"orderNumber": "1011"}
    assert _map_action_result("cancellation_request", None, order_data) is None


# ── Task 3: cancellation result must clearly say "cancelled", not always
# the generic "staged" wording, when Autopilot actually executed it ────────

def test_map_action_result_reports_executed_when_autopilot_completed_it():
    """_maybe_autopilot_cancel mutates the staged dict's status to
    "executed" in-place when Shopify confirms the cancellation in the same
    turn - the widget must render a genuine "Order Cancelled" confirmation,
    not the generic "Cancellation Requested / Awaiting merchant approval"
    staging card, for that case."""
    order_data = {"orderNumber": "1011"}
    result = _map_action_result(
        "cancellation_request", {"success": True, "action_id": "x", "status": "executed"}, order_data,
    )
    assert result == {"type": "cancel_executed", "order_number": "1011", "action_id": "x"}


def test_map_action_result_still_reports_staged_when_status_is_pending():
    """Regression guard: an ordinary Copilot cancellation (human approval
    still required) must keep showing the staged/pending wording, not the
    executed one."""
    order_data = {"orderNumber": "1011"}
    result = _map_action_result(
        "cancellation_request", {"success": True, "action_id": "x", "status": "pending"}, order_data,
    )
    assert result == {"type": "cancel_staged", "order_number": "1011", "action_id": "x"}
