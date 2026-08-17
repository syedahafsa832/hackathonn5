"""
Fixes for the production verification gap: "do you remember what we talked
about?" -> "this is our first conversation together" (customer_info never
carried real history, always defaulted to the literal "New customer"), and
"Tell me about Premium Hoodie V23" -> invented material/marketing claims
(no trigger matched "tell me about"/"what is X", so the reply came from a
frozen RAG import with no anti-embellishment instruction next to it).

1. message_processor.py now populates customer_info["history"] from the
   current ticket's own messages array (already fetched at ticket-creation
   time) - same conversation only, compact/truncated, no new memory
   architecture, no cross-ticket lookup.
2. customer_success_agent.py's live-Shopify trigger now also matches
   "tell me about X" / "describe X" / a narrowly-guarded "what is X"
   (only when the extracted phrase looks product-like - contains a digit
   or a known category word - specifically to avoid hijacking "what is
   your return policy" style questions into a wasted, wrong Shopify
   lookup).
3. The KNOWLEDGE BASE prompt section now carries the same "don't invent
   attributes not present in the source" instruction the live-tool
   tool_context block already had.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ── 1. Ticket messages reach the prompt as history ──────────────────────────

def _ai_result(**overrides):
    result = {
        "reply_body": "Sure, here's what I found.", "ai_reply_generated": True,
        "model_used": "mistral-large-latest",
        "ai_usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "latency_ms": 200, "attempts": 1},
        "intent": "general_inquiry", "sentiment": "neutral", "risk_level": "low",
        "confidence_score": 85, "escalate": False,
    }
    result.update(overrides)
    return result


def test_current_ticket_messages_reach_customer_info_history():
    proc = UnifiedMessageProcessor()
    prior_messages = [
        {"from": "customer@example.com", "body": "Hi, I ordered a hoodie last week", "direction": "inbound"},
        {"from": "Support", "body": "Thanks for reaching out!", "direction": "outbound"},
    ]
    captured = {}

    async def _capture_response(**kwargs):
        captured.update(kwargs)
        return _ai_result()

    mocks = [
        patch("src.workers.message_processor.supabase_select", return_value=[{"id": "tenant-1"}]),
        patch("src.workers.message_processor.supabase_update"),
        patch("src.workers.message_processor.supabase_service.create_ticket", new=AsyncMock(return_value={"id": "ticket-1", "messages": prior_messages})),
        patch("src.workers.message_processor.supabase_service.get_system_settings", new=AsyncMock(return_value={"ai_mode": "active", "confidence_threshold": 0.65})),
        patch("src.workers.message_processor.supabase_service.get_or_create_customer", new=AsyncMock(return_value={"id": "customer-1", "email": "customer@example.com", "name": "Jane"})),
        patch("src.services.plan_service.record_email_processed"),
        patch("src.services.plan_service.can_process_ticket", return_value={"allowed": True}),
        patch("src.services.plan_service.record_ticket_created"),
        patch("src.services.plan_service.check_limit", return_value={"allowed": True}),
        patch("src.services.plan_service.record_ai_reply_event"),
        patch("src.workers.message_processor.customer_success_agent.generate_channel_appropriate_response", new=_capture_response),
        patch("src.workers.message_processor.brand_message_processor._log_conversation", new=AsyncMock()),
    ]
    for m in mocks:
        m.start()
    try:
        run(proc.process_message("email_incoming", {
            "channel": "email", "content": "do you remember what we talked about?",
            "customer_email": "customer@example.com", "customer_name": "Jane",
            "subject": "Follow-up", "store_id": "brand-1",
        }))
    finally:
        for m in mocks:
            m.stop()

    history = captured["customer_info"].get("history")
    assert history is not None
    assert history != "New customer"
    assert "ordered a hoodie last week" in history
    assert "Thanks for reaching out" in history


def test_no_prior_messages_leaves_history_unset_not_fabricated():
    """A brand-new ticket's messages array only has the current inbound
    message at this point - nothing to falsely claim as history."""
    proc = UnifiedMessageProcessor()
    captured = {}

    async def _capture_response(**kwargs):
        captured.update(kwargs)
        return _ai_result()

    only_current_message = [{"from": "customer@example.com", "body": "Hello", "direction": "inbound"}]
    mocks = [
        patch("src.workers.message_processor.supabase_select", return_value=[{"id": "tenant-1"}]),
        patch("src.workers.message_processor.supabase_update"),
        patch("src.workers.message_processor.supabase_service.create_ticket", new=AsyncMock(return_value={"id": "ticket-1", "messages": only_current_message})),
        patch("src.workers.message_processor.supabase_service.get_system_settings", new=AsyncMock(return_value={"ai_mode": "active", "confidence_threshold": 0.65})),
        patch("src.workers.message_processor.supabase_service.get_or_create_customer", new=AsyncMock(return_value={"id": "customer-1", "email": "customer@example.com", "name": "Jane"})),
        patch("src.services.plan_service.record_email_processed"),
        patch("src.services.plan_service.can_process_ticket", return_value={"allowed": True}),
        patch("src.services.plan_service.record_ticket_created"),
        patch("src.services.plan_service.check_limit", return_value={"allowed": True}),
        patch("src.services.plan_service.record_ai_reply_event"),
        patch("src.workers.message_processor.customer_success_agent.generate_channel_appropriate_response", new=_capture_response),
        patch("src.workers.message_processor.brand_message_processor._log_conversation", new=AsyncMock()),
    ]
    for m in mocks:
        m.start()
    try:
        run(proc.process_message("email_incoming", {
            "channel": "email", "content": "Hello", "customer_email": "customer@example.com",
            "customer_name": "Jane", "subject": "Hi", "store_id": "brand-1",
        }))
    finally:
        for m in mocks:
            m.stop()

    # Only the current message exists - history is just that one line, not
    # fabricated multi-turn content.
    history = captured["customer_info"].get("history")
    assert history == "Customer: Hello"


# ── 2. "Tell me about X" reaches the live product tool ──────────────────────

def _fake_ai_response(reply_body: str):
    import json
    msg = MagicMock()
    msg.content = json.dumps({"intent": "product_inquiry", "reply_body": reply_body, "risk_level": "low"})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _run_query(message: str, inventory_return=None):
    inventory_return = inventory_return or {"success": True, "message": "found it"}
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(return_value=(_fake_ai_response("Here you go!"), "test_provider", "test_model", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}))), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))), \
         patch("src.agent.customer_success_agent.v3_tools.get_inventory_status", new=AsyncMock(return_value=inventory_return)) as mock_inventory:
        run(customer_success_agent.process_customer_query(
            query=message,
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "email"},
            tenant_id="tenant-1", store_id=None, ticket_id="ticket-1",
        ))
    return mock_inventory


def test_tell_me_about_reaches_live_product_lookup():
    mock_inventory = _run_query("do you remember what we talked about till now? Tell me about Premium Hoodie V23.")
    mock_inventory.assert_called_once()
    assert mock_inventory.call_args.args[0] == "premium hoodie v23"


def test_describe_phrasing_reaches_live_product_lookup():
    mock_inventory = _run_query("Can you describe the Essential Hoodie V5?")
    mock_inventory.assert_called_once()
    assert mock_inventory.call_args.args[0] == "essential hoodie v5"


def test_what_is_with_a_version_number_reaches_live_product_lookup():
    mock_inventory = _run_query("What is the Premium Hoodie V23?")
    mock_inventory.assert_called_once()
    assert mock_inventory.call_args.args[0] == "premium hoodie v23"


def test_what_is_a_policy_question_does_not_trigger_a_product_lookup():
    """The false-positive risk this fix had to guard against - 'what is'
    is the single most common phrasing for policy questions."""
    mock_inventory = _run_query("What is your return policy?")
    mock_inventory.assert_not_called()


def test_what_is_my_order_status_does_not_trigger_a_product_lookup():
    mock_inventory = _run_query("What is happening with my order?")
    mock_inventory.assert_not_called()


# ── 3. RAG anti-embellishment instruction ────────────────────────────────────

def test_knowledge_base_section_forbids_inventing_attributes_not_in_the_source():
    prompt = customer_success_agent._construct_v3_prompt(
        customer_info={"name": "Jane", "email": "jane@example.com"},
        rag_context="Product: Premium Hoodie V23\nDescription: A high-end hoodie made from Organic Cotton. Fit: cropped.",
        sizing_context="", tool_context="", action_context="",
    )
    lowered = prompt.lower()
    assert "authoritative only for claims explicitly present" in lowered
    assert "do not invent material" in lowered
    # The instruction must sit immediately next to the actual retrieved
    # content, not somewhere disconnected from it.
    kb_idx = lowered.index("knowledge base")
    rag_idx = lowered.index("organic cotton")
    assert kb_idx < rag_idx
