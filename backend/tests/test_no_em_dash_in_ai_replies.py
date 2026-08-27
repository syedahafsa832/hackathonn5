"""
GLOBAL no-em-dash rule: no AI-generated customer-facing reply (email, chat,
widget, draft) may ever contain the em dash character (—), in any brand or
channel. Model output frequently includes em dashes on its own (e.g. "All
good here — just hanging out"), and this app's own hardcoded sign-off used
one too (f"— {agent_name}"). Both are covered:

1. customer_success_agent._strip_em_dash() sanitizes the model's reply_body
   immediately after extraction (before this module's own greeting/signature
   text is appended), so the fix applies regardless of channel or brand.
2. Every hardcoded "— {agent_name}" / "— The {brand} Team" sign-off literal
   in the codebase was changed to a plain hyphen, so this module's own
   appended text can never reintroduce the character it just stripped.

Never touches the customer's own message or conversation history - only
this function's generated reply_body.
"""
import os
import sys
import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent, _strip_em_dash  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402

EM_DASH = "—"


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


NO_ACTION = IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm")
_FAKE_USAGE = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 1, "attempts": 1}


# ── 1. Unit behavior of the sanitizer itself ──────────────────────────────

def test_strip_em_dash_converts_spaced_dash_to_comma():
    text = "Hey there! All good here — just hanging out, ready to help."
    result = _strip_em_dash(text)
    assert EM_DASH not in result
    assert "here, just hanging out" in result


def test_strip_em_dash_converts_unspaced_dash_to_hyphen():
    assert _strip_em_dash("word—word") == "word-word"


def test_strip_em_dash_is_a_noop_when_absent():
    text = "Hey there! All good here, just hanging out."
    assert _strip_em_dash(text) == text


def test_strip_em_dash_handles_empty_and_none_input():
    assert _strip_em_dash("") == ""
    assert _strip_em_dash(None) is None


# ── 2. End-to-end: the exact reported case, model output containing an
# em dash must never reach the returned reply_body ────────────────────────

def _fake_ai_response(reply_body: str):
    msg = MagicMock()
    msg.content = json.dumps({"intent": "general_inquiry", "reply_body": reply_body, "risk_level": "low"})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def test_em_dash_in_raw_model_output_never_reaches_the_customer_facing_reply():
    dashy_reply = "Hey there! All good here — just hanging out, ready to help. How about you? 😊"

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion",
               new=AsyncMock(return_value=(_fake_ai_response(dashy_reply), "test_provider", "test_model", _FAKE_USAGE))), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=NO_ACTION)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.lib.supabase_client.supabase_select", return_value=[]):
        result = run(customer_success_agent.process_customer_query(
            query="hiiii",
            customer_info={"name": "Sam", "email": "sam@example.com", "channel": "chat"},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
        ))

    assert EM_DASH not in result["reply_body"]


def test_no_email_signature_sign_off_never_contains_an_em_dash():
    """No custom email_signature configured -> the module's own generated
    sign-off is appended. It must be a plain hyphen, never an em dash."""
    brand_row = {"id": "brand-1", "name": "Test Brand", "agent_name": "Zoe", "email_signature": None}

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion",
               new=AsyncMock(return_value=(_fake_ai_response("Sure, happy to help with that!"), "test_provider", "test_model", _FAKE_USAGE))), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=NO_ACTION)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.lib.supabase_client.supabase_select", return_value=[brand_row]), \
         patch("src.services.reply_style_service.supabase_select", return_value=[]):
        result = run(customer_success_agent.process_customer_query(
            query="can you help me?",
            customer_info={"name": "Sam", "email": "sam@example.com", "channel": "email"},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
        ))

    assert EM_DASH not in result["reply_body"]
    assert "- Zoe" in result["reply_body"]


def test_fallback_and_provider_failure_responses_never_contain_an_em_dash():
    """Canned fallback/outage responses go through a completely different
    return path (no model call at all) - their own hardcoded sign-off must
    still be em-dash-free."""
    fb = customer_success_agent._get_fallback_response("boom", brand_name="Test Brand", agent_name="Zoe")
    assert EM_DASH not in fb["reply_body"]

    # Default (customer-facing fallback disabled): reply_body is empty, so
    # there's nothing to check - the real assertion is exercising the
    # non-empty branch below, where the sign-off is actually appended.
    pf_default = customer_success_agent._get_provider_failure_response(brand_name="Test Brand", agent_name="Zoe")
    assert EM_DASH not in pf_default["reply_body"]

    pf_enabled = customer_success_agent._get_provider_failure_response(
        brand_name="Test Brand", agent_name="Zoe", send_customer_fallback=True,
    )
    assert EM_DASH not in pf_enabled["reply_body"]
    assert "- Zoe" in pf_enabled["reply_body"]


def test_em_dash_stripping_does_not_touch_the_customer_own_message():
    """Scope check: the sanitizer must only ever run on the generated
    reply_body, never on the inbound customer query itself."""
    customer_message_with_dash = "Hi — I have a question about my order — thanks!"
    dashy_reply = "Sure — happy to help with that."

    captured = {}

    async def _capture(*, messages, **kwargs):
        captured["messages"] = messages
        return _fake_ai_response(dashy_reply), "test_provider", "test_model", _FAKE_USAGE

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=_capture)), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=NO_ACTION)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.lib.supabase_client.supabase_select", return_value=[]):
        result = run(customer_success_agent.process_customer_query(
            query=customer_message_with_dash,
            customer_info={"name": "Sam", "email": "sam@example.com", "channel": "chat"},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
        ))

    # The customer's own message, as sent to the model, is untouched.
    user_message = next(m["content"] for m in captured["messages"] if m["role"] == "user")
    assert EM_DASH in user_message
    # But the generated reply the customer will actually see has none.
    assert EM_DASH not in result["reply_body"]
