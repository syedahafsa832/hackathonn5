"""
End-to-end wiring test for the multi-turn pending-action continuation fix,
driven through the REAL customer_success_agent.process_customer_query()
entry point (not just the intent_detector/return_actions unit in isolation).

Reproduces the exact reported bug at the integration level:
  Turn 1: "Can I cancel order #1009?" stages a real `actions` row
          (status="pending", action_type="cancel", order_number="1009").
  Turn 2: "yes, please go ahead" - no order number, no verb of its own.

This proves the full chain: process_customer_query() looks up the durable
pending action for this ticket BEFORE calling intent_detector.detect(),
passes it into the SAME prompt used for classifying any fresh message
(captured here via a fake LLM client - this is what proves the plumbing;
genuine judgment of arbitrary customer phrasing needs a live model and
is intentionally NOT what this test claims to verify), and the resolved
action_type/order_id flow into return_actions.handle_return_intent()
completely unchanged - i.e. the full eligibility/approval/duplicate-action
pipeline in return_actions_integration.py still gets to run and is never
bypassed by context reuse.
"""
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent  # noqa: E402
from src.services.intent_detector import intent_detector as real_intent_detector  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


_FAKE_USAGE = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}


def _fake_final_ai_response():
    content = json.dumps({"intent": "cancellation_request", "reply_body": "Your cancellation has been forwarded for review.", "risk_level": "low"})
    return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])


def _make_fake_intent_client(response_json, captured):
    class _FakeMessage:
        content = json.dumps(response_json)

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = None

    def fake_create(**kwargs):
        captured["intent_prompt"] = kwargs["messages"][0]["content"]
        return _FakeResponse()

    return type("FakeClient", (), {
        "chat": type("Chat", (), {"completions": type("Completions", (), {"create": staticmethod(fake_create)})()})(),
    })()


def _run_turn_2(pending_actions_return, intent_llm_response, ticket_rows=None):
    """Drives process_customer_query for the turn-2 "yes, please go ahead"
    message, with every collaborator mocked except the real intent_detector
    and the real pending-action wiring inside customer_success_agent.py."""
    captured = {}

    async def capturing_completion(*args, messages=None, **kwargs):
        captured["final_messages"] = messages
        return (_fake_final_ai_response(), "test_provider", "test_model", _FAKE_USAGE)

    def fake_select(table, params=None):
        if table == "tickets":
            return ticket_rows or []
        return []

    fake_intent_client = _make_fake_intent_client(intent_llm_response, captured)
    find_pending_mock = AsyncMock(return_value=pending_actions_return)
    handle_return_intent_mock = AsyncMock(return_value={"action_context": "", "staged": True})

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=capturing_completion), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.agent.customer_success_agent.return_actions.find_pending_actions_for_ticket", new=find_pending_mock), \
         patch("src.agent.customer_success_agent.return_actions.handle_return_intent", new=handle_return_intent_mock), \
         patch.object(real_intent_detector, "_get_client", return_value=fake_intent_client), \
         patch("src.lib.supabase_client.supabase_select", side_effect=fake_select):
        run(customer_success_agent.process_customer_query(
            query="yes, please go ahead",
            customer_info={"name": "Syeda", "email": "customer@example.com"},
            tenant_id="tenant-1", store_id="brand-1", ticket_id="ticket-1",
        ))

    return captured, find_pending_mock, handle_return_intent_mock


# ── The reported bug, reproduced and proven fixed end to end ──────────────

def test_bare_affirmative_resolves_via_durable_pending_action_not_a_phrase_list():
    pending_row = {"action_type": "cancel", "order_number": "1009", "status": "pending"}
    captured, find_pending_mock, handle_return_intent_mock = _run_turn_2(
        pending_actions_return=[pending_row],
        intent_llm_response={"action_type": "cancel", "order_id": "1009", "confidence": 0.9},
    )

    find_pending_mock.assert_awaited_once_with("ticket-1")
    assert "PENDING ACTION CONTEXT" in captured["intent_prompt"]
    assert "1009" in captured["intent_prompt"]

    handle_return_intent_mock.assert_awaited_once()
    _, kwargs = handle_return_intent_mock.await_args
    resolved = kwargs["intent_result"]
    assert resolved.action_type == "cancel"
    assert resolved.order_id == "1009"


# ── Negative case: no pending action -> no context, no guess ──────────────

def test_bare_yes_with_no_pending_action_never_invents_one():
    captured, find_pending_mock, handle_return_intent_mock = _run_turn_2(
        pending_actions_return=[],
        intent_llm_response={"action_type": "none", "confidence": 0.9},
    )

    find_pending_mock.assert_awaited_once_with("ticket-1")
    assert "PENDING ACTION CONTEXT" not in captured["intent_prompt"]
    handle_return_intent_mock.assert_not_awaited()


# ── Multiple orders pending -> genuine ambiguity, never "last mentioned" ──

def test_two_pending_actions_for_different_orders_refuses_to_guess():
    pending_rows = [
        {"action_type": "cancel", "order_number": "1009", "status": "pending"},
        {"action_type": "refund", "order_number": "1005", "status": "pending"},
    ]
    captured, find_pending_mock, handle_return_intent_mock = _run_turn_2(
        pending_actions_return=pending_rows,
        intent_llm_response={"action_type": "none", "confidence": 0.9},
    )

    find_pending_mock.assert_awaited_once_with("ticket-1")
    assert "PENDING ACTION CONTEXT" not in captured["intent_prompt"]
    handle_return_intent_mock.assert_not_awaited()


# ── Explicit order switch: customer names a different order ───────────────

def test_customer_naming_a_different_order_switches_never_inherits_old_authorization():
    """The prompt instructs the model to classify based on the NEW order
    when the customer names one explicitly; here we simulate that judgment
    (a live model call is what would actually make it) and verify the
    resolved order flows through untouched - i.e. nothing downstream
    forces the OLD pending order back in once the model has switched."""
    pending_row = {"action_type": "cancel", "order_number": "1009", "status": "pending"}
    captured, find_pending_mock, handle_return_intent_mock = _run_turn_2(
        pending_actions_return=[pending_row],
        intent_llm_response={"action_type": "cancel", "order_id": "2042", "confidence": 0.9},
    )

    assert "PENDING ACTION CONTEXT" in captured["intent_prompt"]
    handle_return_intent_mock.assert_awaited_once()
    _, kwargs = handle_return_intent_mock.await_args
    resolved = kwargs["intent_result"]
    assert resolved.order_id == "2042"


# ── Unrelated follow-up must not be forced into the pending action ────────

def test_unrelated_followup_after_pending_cancellation_is_not_treated_as_confirmation():
    """Simulates the model correctly recognizing an unrelated question
    despite a pending action existing (prompt still carries the context,
    but the resolution below - which only a live LLM could truly make -
    is 'none', as it must be for an off-topic message)."""
    pending_row = {"action_type": "cancel", "order_number": "1009", "status": "pending"}
    captured, find_pending_mock, handle_return_intent_mock = _run_turn_2(
        pending_actions_return=[pending_row],
        intent_llm_response={"action_type": "none", "confidence": 0.9},
    )

    assert "PENDING ACTION CONTEXT" in captured["intent_prompt"]
    handle_return_intent_mock.assert_not_awaited()


# ── Security boundary: identity/approval pipeline is still invoked as-is ──

def test_resolved_action_still_goes_through_the_full_handle_return_intent_pipeline():
    """The fix only ever decides WHICH action_type/order_id reach
    handle_return_intent() - it must still be handle_return_intent() (the
    single source of truth for eligibility/approval/duplicate-action
    checks in return_actions_integration.py) that gets called, with all
    its normal keyword arguments, never a shortcut that skips it."""
    pending_row = {"action_type": "cancel", "order_number": "1009", "status": "pending"}
    _, _, handle_return_intent_mock = _run_turn_2(
        pending_actions_return=[pending_row],
        intent_llm_response={"action_type": "cancel", "order_id": "1009", "confidence": 0.9},
    )
    handle_return_intent_mock.assert_awaited_once()
    _, kwargs = handle_return_intent_mock.await_args
    assert kwargs["tenant_id"] == "tenant-1"
    assert kwargs["brand_id"] == "brand-1"
    assert kwargs["ticket_id"] == "ticket-1"
