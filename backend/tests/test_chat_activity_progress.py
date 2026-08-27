"""
Chat activity/status states.

Goal: the customer never sees a dead/blank loading state while the AI is
working, and every activity label shown must reflect what the system is
actually doing - never a frontend-invented guess.

process_customer_query() now accepts an optional on_progress(stage, label)
callback, invoked synchronously immediately before each real dispatch point
(order lookup, product lookup, product search, shipping lookup, policy
check, final generation) actually runs - never invented, and never fired for
a branch that didn't run. These tests cover:

1. An activity state appears at the very start of every call (on_progress is
   never left uncalled while the AI is working - no dead spinner).
2. The stage/label sequence matches which real tool actually ran for a given
   query - an order question shows order-lookup wording, a product question
   shows product wording, a policy-only question shows neither, and an
   unrelated query never shows a status for a lookup that didn't happen.
3. Nothing internal (the raw query, prompts, tool arguments, order numbers,
   emails) ever appears inside a label - only the fixed, generic phrase.
4. on_progress is optional and best-effort: omitting it (existing callers -
   email channel, workers) is unaffected, and a callback that itself raises
   never breaks query processing.
"""
import os
import sys
import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402


def run(coro):
    # See test_chat_widget_action_staging.py - a prior @pytest.mark.asyncio
    # test can leave a closed loop as the thread's "current" loop.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _fake_ai_response(reply_body: str, intent: str = "order_status_inquiry"):
    content = json.dumps({"intent": intent, "reply_body": reply_body, "risk_level": "low"})
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


NO_ACTION = IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm")

_FAKE_USAGE = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}


def _run(query: str, *, order_status=None, inventory=None, recommendations=None, discovery=None, rag_context=""):
    """Runs process_customer_query for real with only the network-touching
    edges mocked, and captures every on_progress(stage, label) call in
    order. store_id is a real (truthy) value - rag_context is only ever
    fetched when store_id is set (see process_customer_query), and this file
    covers the policy_check stage, which depends on it - the brand/Shopify-
    credential Supabase lookup that store_id also triggers is neutralized by
    patching supabase_select to return no rows rather than hitting a real
    (or unreachable) Supabase instance."""
    events = []

    async def on_progress(stage, label):
        events.append((stage, label))

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion",
               new=AsyncMock(return_value=(_fake_ai_response("Here you go!"), "test_provider", "test_model", _FAKE_USAGE))), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value=order_status or {"success": False})), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.v3_tools.get_inventory_status", new=AsyncMock(return_value=inventory or {"success": False, "message": "not found"})), \
         patch("src.agent.customer_success_agent.v3_tools.get_product_recommendations", new=AsyncMock(return_value=recommendations or {"success": False, "message": "none"})), \
         patch("src.agent.customer_success_agent.v3_tools.discover_products_by_category", new=AsyncMock(return_value=discovery or {"success": False, "message": "none"})), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=NO_ACTION)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value=rag_context)), \
         patch("src.lib.supabase_client.supabase_select", return_value=[]):
        result = run(customer_success_agent.process_customer_query(
            query=query,
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
            on_progress=on_progress,
        ))
    return result, events


# ── 1. An activity state always appears, immediately ─────────────────────

def test_activity_state_appears_immediately_for_every_query():
    _, events = _run("what are your store hours?")
    assert events, "on_progress was never called - customer would see a dead loading state"
    assert events[0] == ("thinking", "Analyzing request…")


# ── 2. The stage shown matches what actually ran ──────────────────────────

def test_order_question_surfaces_order_lookup_stage_only():
    _, events = _run(
        "where is my order #1234?",
        order_status={"success": True, "order_number": "1234", "status": "fulfilled", "items": [], "financial_status": "paid"},
    )
    stages = [s for s, _ in events]
    assert "order_lookup" in stages
    assert "product_lookup" not in stages
    assert "product_search" not in stages
    assert "policy_check" not in stages


def test_order_with_live_tracking_also_surfaces_shipping_lookup_stage():
    order = {
        "success": True, "order_number": "1234", "status": "fulfilled", "items": [],
        "financial_status": "paid", "tracking_number": "TRACK123", "tracking_company": "USPS",
        "tracking_url": "https://example.com/track/TRACK123",
    }
    brand_row = {
        "name": "Test Brand", "agent_name": "Luna",
        "shopify_connected": True, "shopify_access_token": "enc-token", "shopify_domain": "test.myshopify.com",
        "aftership_api_key": "aftership-key",
    }
    events = []

    async def on_progress(stage, label):
        events.append((stage, label))

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion",
               new=AsyncMock(return_value=(_fake_ai_response("It's on the way!"), "test_provider", "test_model", _FAKE_USAGE))), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value=order)), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=NO_ACTION)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.lib.supabase_client.supabase_select", return_value=[brand_row]), \
         patch("src.services.shopify_service.decrypt_token", return_value="decrypted-token"), \
         patch("src.services.tracking_service.shopify_carrier_to_aftership_slug", return_value="usps"), \
         patch("src.services.tracking_service.get_tracking_status", new=AsyncMock(return_value={"status": "in_transit"})):
        run(customer_success_agent.process_customer_query(
            query="where is my order #1234?",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
            on_progress=on_progress,
        ))

    stages = [s for s, _ in events]
    assert "order_lookup" in stages
    assert "shipping_lookup" in stages
    # Shipping status must only appear after the order it depends on
    assert stages.index("shipping_lookup") > stages.index("order_lookup")


def test_product_question_surfaces_product_lookup_stage_only():
    _, events = _run(
        "do you have the Essential Hoodie in stock?",
        inventory={"success": True, "message": "In stock"},
    )
    stages = [s for s, _ in events]
    assert "product_lookup" in stages
    assert "order_lookup" not in stages
    assert "product_search" not in stages
    assert "policy_check" not in stages


def test_product_found_only_appears_when_shopify_lookup_actually_succeeded():
    _, events = _run(
        "do you have the Essential Hoodie in stock?",
        inventory={"success": True, "message": "In stock"},
    )
    stages = [s for s, _ in events]
    assert "product_found" in stages
    assert stages.index("product_found") > stages.index("product_lookup")


def test_product_not_found_never_claims_shopify_product_found():
    _, events = _run(
        "do you have the Essential Hoodie in stock?",
        inventory={"success": False, "message": "I couldn't find that."},
    )
    stages = [s for s, _ in events]
    assert "product_lookup" in stages
    assert "product_found" not in stages


def test_order_found_only_appears_when_shopify_lookup_actually_succeeded():
    _, events = _run(
        "where is my order #1234?",
        order_status={"success": True, "order_number": "1234", "status": "fulfilled", "items": [], "financial_status": "paid"},
    )
    stages = [s for s, _ in events]
    assert "order_found" in stages
    assert stages.index("order_found") > stages.index("order_lookup")


def test_failed_order_lookup_never_claims_shopify_order_found():
    _, events = _run(
        "where is my order #1234?",
        order_status={"success": False, "message": "Order not found"},
    )
    stages = [s for s, _ in events]
    assert "order_lookup" in stages
    assert "order_found" not in stages


def test_recommendation_question_surfaces_product_search_stage_only():
    _, events = _run(
        "do you have anything similar to the Essential Hoodie?",
        recommendations={"success": True, "recommendations": [], "message": "Here are some options"},
    )
    stages = [s for s, _ in events]
    assert "product_search" in stages
    assert "product_lookup" not in stages
    assert "order_lookup" not in stages


def test_policy_question_surfaces_policy_check_stage_when_knowledge_base_has_content():
    _, events = _run("what is your return policy?", rag_context="Our return policy: 30 days, unworn.")
    stages = [s for s, _ in events]
    assert "policy_check" in stages
    assert "order_lookup" not in stages
    assert "product_lookup" not in stages
    assert "product_search" not in stages


def test_policy_question_without_knowledge_base_content_does_not_fabricate_policy_check_stage():
    """No RAG content means there's genuinely nothing being 'checked' -
    showing 'Checking our policies…' anyway would be exactly the kind of
    fake activity this feature must not produce."""
    _, events = _run("what is your return policy?", rag_context="")
    stages = [s for s, _ in events]
    assert "policy_check" not in stages
    assert "preparing" in stages


def test_kb_check_stage_only_appears_when_a_store_id_is_provided():
    """kb_check must reflect the real get_brand_context() call, which only
    ever runs when store_id is set (see process_customer_query)."""
    _, events = _run("what are your store hours?")
    stages = [s for s, _ in events]
    assert "kb_check" in stages  # _run always passes store_id="brand-1"
    assert stages.index("thinking") < stages.index("kb_check")  # sanity: near the start


def test_style_check_stage_only_appears_when_a_brand_row_was_actually_found():
    """style_check must reflect the real reply-style/examples resolution,
    which only runs inside the `if _b:` branch once a brand row is found —
    never fabricated when no brand exists for store_id."""
    # _run's default supabase_select mock returns [] for every table -
    # no brand row is ever found, so style_check must not fire.
    _, events = _run("what are your store hours?")
    stages = [s for s, _ in events]
    assert "style_check" not in stages


def test_style_check_stage_appears_when_a_brand_row_is_found():
    brand_row = {"id": "brand-1", "name": "Test Brand", "agent_name": "Luna", "reply_style_mode": "preset", "reply_style_preset": "warm_friendly"}
    events = []

    async def on_progress(stage, label):
        events.append((stage, label))

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion",
               new=AsyncMock(return_value=(_fake_ai_response("Here you go!"), "test_provider", "test_model", _FAKE_USAGE))), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=NO_ACTION)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.lib.supabase_client.supabase_select", return_value=[brand_row]), \
         patch("src.services.reply_style_service.supabase_select", return_value=[]):
        run(customer_success_agent.process_customer_query(
            query="what are your store hours?",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
            on_progress=on_progress,
        ))

    stages = [s for s, _ in events]
    assert "style_check" in stages


def test_final_generation_stage_always_precedes_the_model_call():
    _, events = _run("hi there!")
    stages = [s for s, _ in events]
    assert "preparing" in stages
    # 'preparing' is the last status before the reply is generated
    assert stages[-1] == "preparing"


# ── 3. No internal details ever leak into a label ─────────────────────────

def test_activity_labels_never_leak_internal_details():
    """The order number is expected in the label now ("Finding order
    #98765...") - it's customer-supplied, public-facing, and the resolution
    activity UI is specifically designed to show it (see
    test_cancel_order_number_routing.py's progress-event tests). What must
    never leak is anything actually sensitive/internal: the customer's
    email, system/prompt/implementation details, or an oversized dump."""
    _, events = _run(
        "where is my order #98765, my email is secret@customer.com?",
        order_status={"success": True, "order_number": "98765", "status": "fulfilled", "items": [], "financial_status": "paid"},
    )
    assert events
    for stage, label in events:
        assert "secret@customer.com" not in label
        assert "system" not in label.lower()
        assert "prompt" not in label.lower()
        # A real activity phrase, not a dumped prompt/trace/tool-call payload
        assert len(label) < 60
        assert stage.replace("_", "").isalnum()


# ── 4. Best-effort and backward-compatible ────────────────────────────────

def test_on_progress_is_optional_existing_callers_unaffected():
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion",
               new=AsyncMock(return_value=(_fake_ai_response("Hello!"), "test_provider", "test_model", _FAKE_USAGE))), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value={"success": False})), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=NO_ACTION)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")):
        result = run(customer_success_agent.process_customer_query(
            query="hello",
            customer_info={"name": "Jane", "email": "", "channel": "chat"},
            tenant_id="tenant-1",
            store_id=None,
            ticket_id="ticket-1",
            # on_progress deliberately omitted
        ))
    assert result.get("reply_body")


def test_broken_on_progress_callback_never_breaks_processing():
    async def bad_progress(stage, label):
        raise RuntimeError("boom")

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion",
               new=AsyncMock(return_value=(_fake_ai_response("Hello!"), "test_provider", "test_model", _FAKE_USAGE))), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value={"success": False})), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=NO_ACTION)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")):
        result = run(customer_success_agent.process_customer_query(
            query="hello",
            customer_info={"name": "Jane", "email": "", "channel": "chat"},
            tenant_id="tenant-1",
            store_id=None,
            ticket_id="ticket-1",
            on_progress=bad_progress,
        ))
    assert result.get("reply_body")
