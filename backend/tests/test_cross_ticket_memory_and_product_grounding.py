"""
Complete fix pass for two production verification failures:

1. "what do you remember about our conversation till now?" -> "this is the
   first message you've sent my way" - despite the customer having multiple
   PRIOR tickets. The previous fix (test_conversation_history_and_grounding.py)
   only covered the current ticket's own messages; this file covers the
   cross-ticket memory now added on top of it (message_processor.py
   _build_customer_history), store-scoped, bounded, current-ticket excluded.

2. "Is Premium Hoodie V23 super soft and durable?" -> invented "Pima Cotton"
   / "exceptional softness" / "long-lasting quality" - none of which exist
   in the real Shopify data ("Organic Cotton. Fit: cropped."). Root cause
   was two-fold: (a) product-detail questions in this phrasing never
   reached the live Shopify lookup at all (narrow keyword list), and (b)
   even when a lookup DID run, get_inventory_status() never returned the
   product's real description or price, so the model had nothing
   authoritative to answer material/fit/price questions with and filled
   the gap by inventing plausible-sounding claims.

Also covers the STAGE 1.5 same-Gmail-thread fix: a reply in an existing
thread previously never reached AI generation at all (silently appended
and stopped) - now it's a genuine continuation of the same pipeline,
which is also what makes accumulated real conversation history actually
possible for repeat email exchanges, not just cross-ticket lookups.
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
from src.services.shopify_service import ShopifyClient  # noqa: E402
from src.services.tools import V3Tools  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ═══════════════════════════════════════════════════════════════════════════
# PART 1/8/10 — CROSS-TICKET CUSTOMER MEMORY
# ═══════════════════════════════════════════════════════════════════════════

def _ticket(id_, subject, messages, store_id="brand-1"):
    return {"id": id_, "subject": subject, "messages": messages, "store_id": store_id}


def _msgs(*pairs):
    """pairs like [("in", "hi"), ("out", "hello")]"""
    return [
        {"from": "x", "body": body, "direction": "inbound" if d == "in" else "outbound"}
        for d, body in pairs
    ]


def test_previous_ticket_history_reaches_the_prompt():
    proc = UnifiedMessageProcessor()
    prior = _ticket("prior-1", "Order question", _msgs(("in", "Where is my order 1234?"), ("out", "It shipped yesterday!")))

    with patch("src.workers.message_processor.supabase_select", return_value=[prior]):
        history = run(proc._build_customer_history("customer@example.com", "brand-1", "current-ticket", []))

    assert history is not None
    assert "Previous conversation (Order question)" in history
    assert "Where is my order 1234" in history
    assert "It shipped yesterday" in history


def test_current_ticket_excluded_from_previous_ticket_history():
    proc = UnifiedMessageProcessor()
    current = _ticket("current-1", "Now", _msgs(("in", "current message")))
    other = _ticket("prior-1", "Earlier", _msgs(("in", "earlier message")))

    with patch("src.workers.message_processor.supabase_select", return_value=[current, other]):
        history = run(proc._build_customer_history("customer@example.com", "brand-1", "current-1", []))

    assert "earlier message" in history
    # The current ticket must only appear via the current_ticket_messages
    # argument, never duplicated in from the "previous tickets" query.
    assert history.count("current message") == 0


def test_history_is_bounded_to_a_small_number_of_prior_tickets():
    proc = UnifiedMessageProcessor()
    many_prior = [_ticket(f"t{i}", f"Subject {i}", _msgs(("in", f"message {i}"))) for i in range(10)]

    with patch("src.workers.message_processor.supabase_select", return_value=many_prior):
        history = run(proc._build_customer_history("customer@example.com", "brand-1", "current-ticket", []))

    included = sum(1 for i in range(10) if f"message {i}" in history)
    assert included <= UnifiedMessageProcessor._HISTORY_MAX_PRIOR_TICKETS


def test_history_is_bounded_in_total_length_not_unlimited():
    proc = UnifiedMessageProcessor()
    huge_message = "x" * 5000
    prior = [_ticket("t1", "Long", _msgs(("in", huge_message)))]

    with patch("src.workers.message_processor.supabase_select", return_value=prior):
        history = run(proc._build_customer_history("customer@example.com", "brand-1", "current-ticket", []))

    assert len(history) <= UnifiedMessageProcessor._HISTORY_MAX_TOTAL_CHARS + 3  # +3 for "..."


def test_no_previous_history_returns_none_not_fabricated_memory():
    proc = UnifiedMessageProcessor()
    with patch("src.workers.message_processor.supabase_select", return_value=[]):
        history = run(proc._build_customer_history("newcustomer@example.com", "brand-1", None, []))
    assert history is None


def test_same_email_in_a_different_store_cannot_leak_history():
    """The exact tenant-isolation requirement: brand A's history for this
    customer must never reach brand B, even though the email matches."""
    proc = UnifiedMessageProcessor()
    captured_params = {}

    def fake_select(table, params=None):
        captured_params.update(params or {})
        # Simulate a real store-scoped query: only returns rows if the
        # store_id filter actually matches brand A's data - never returns
        # brand A's ticket just because supabase_select was called at all.
        if params and params.get("store_id") == "eq.brand-A":
            return [_ticket("a1", "Brand A history", _msgs(("in", "Premium Hoodie V23 question")), store_id="brand-A")]
        return []

    with patch("src.workers.message_processor.supabase_select", side_effect=fake_select):
        history_for_brand_b = run(proc._build_customer_history("shared@example.com", "brand-B", None, []))

    assert captured_params.get("store_id") == "eq.brand-B"
    assert history_for_brand_b is None
    assert "Premium Hoodie V23" not in (history_for_brand_b or "")


def test_multiple_previous_tickets_are_queried_in_recent_first_order():
    proc = UnifiedMessageProcessor()
    captured_params = {}

    def fake_select(table, params=None):
        captured_params.update(params or {})
        return []

    with patch("src.workers.message_processor.supabase_select", side_effect=fake_select):
        run(proc._build_customer_history("customer@example.com", "brand-1", None, []))

    assert captured_params.get("order") == "updated_at.desc"


def test_history_formatting_uses_understandable_customer_support_labels():
    proc = UnifiedMessageProcessor()
    prior = _ticket("t1", "Sizing question", _msgs(("in", "What size should I get?"), ("out", "I'd recommend a Medium.")))
    with patch("src.workers.message_processor.supabase_select", return_value=[prior]):
        history = run(proc._build_customer_history("customer@example.com", "brand-1", "current", []))

    assert "Customer: What size should I get?" in history
    assert "Support: I'd recommend a Medium." in history


def test_one_previous_ticket_with_no_messages_is_skipped_not_shown_as_blank():
    proc = UnifiedMessageProcessor()
    empty_ticket = _ticket("t1", "Empty", [])
    with patch("src.workers.message_processor.supabase_select", return_value=[empty_ticket]):
        history = run(proc._build_customer_history("customer@example.com", "brand-1", "current", []))
    assert history is None


# ── Full pipeline: cross-ticket history actually reaches customer_info ─────

def _ai_result(**overrides):
    result = {
        "reply_body": "Sure!", "ai_reply_generated": True, "model_used": "mistral-large-latest",
        "ai_usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "latency_ms": 200, "attempts": 1},
        "intent": "general_inquiry", "sentiment": "neutral", "risk_level": "low",
        "confidence_score": 85, "escalate": False,
    }
    result.update(overrides)
    return result


def _run_process_message_capturing_customer_info(message, prior_tickets=None):
    proc = UnifiedMessageProcessor()
    captured = {}

    async def _capture_response(**kwargs):
        captured.update(kwargs)
        return _ai_result()

    def fake_select(table, params=None):
        if table == "tickets" and params and "customer_email" in params:
            return prior_tickets or []
        if table == "tenants":
            return [{"id": "tenant-1"}]
        return []

    mocks = [
        patch("src.workers.message_processor.supabase_select", side_effect=fake_select),
        patch("src.workers.message_processor.supabase_update"),
        patch("src.workers.message_processor.supabase_service.create_ticket", new=AsyncMock(return_value={"id": "current-ticket", "messages": []})),
        patch("src.workers.message_processor.supabase_service.get_system_settings", new=AsyncMock(return_value={"ai_mode": "active", "confidence_threshold": 0.65})),
        patch("src.workers.message_processor.supabase_service.get_or_create_customer", new=AsyncMock(return_value={"id": "customer-1", "email": "aicoders123@gmail.com", "name": "Jane"})),
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
        run(proc.process_message("email_incoming", message))
    finally:
        for m in mocks:
            m.stop()
    return captured


def test_returning_customer_gets_real_prior_conversation_in_the_prompt():
    """The exact production bug: aicoders123@gmail.com has prior
    conversations but was told 'this is the first message'."""
    prior = [_ticket("prior-1", "Product question", _msgs(("in", "Tell me about Premium Hoodie V23"), ("out", "It's made from Organic Cotton, cropped fit.")))]

    captured = _run_process_message_capturing_customer_info({
        "channel": "email", "content": "what do you remember about our conversation till now?",
        "customer_email": "aicoders123@gmail.com", "customer_name": "Jane",
        "subject": "Follow-up", "store_id": "brand-1",
    }, prior_tickets=prior)

    history = captured["customer_info"].get("history")
    assert history is not None
    assert history != "New customer"
    assert "Premium Hoodie V23" in history
    assert "Organic Cotton" in history


# ═══════════════════════════════════════════════════════════════════════════
# PART 3/7 — GENERAL PRODUCT-MENTION ROUTING (not a tiny phrase keyword list)
# ═══════════════════════════════════════════════════════════════════════════

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


def test_what_material_is_x_reaches_live_lookup():
    """'What material is X' has no contiguous 'what is' or 'tell me
    about'/'describe' phrase - only the general fallback catches this."""
    mock_inventory = _run_query("What material is Premium Hoodie V23?")
    mock_inventory.assert_called_once()
    assert mock_inventory.call_args.args[0] == "premium hoodie v23"


def test_how_does_it_fit_reaches_live_lookup():
    mock_inventory = _run_query("How does Premium Hoodie V23 fit?")
    mock_inventory.assert_called_once()
    assert mock_inventory.call_args.args[0] == "premium hoodie v23"


def test_is_it_soft_and_durable_reaches_live_lookup():
    """The exact production query."""
    mock_inventory = _run_query("Is Premium Hoodie V23 super soft and durable?")
    mock_inventory.assert_called_once()
    assert mock_inventory.call_args.args[0] == "premium hoodie v23"


def test_does_it_come_in_cotton_reaches_live_lookup():
    mock_inventory = _run_query("Does Hoodie V23 come in cotton?")
    mock_inventory.assert_called_once()
    assert mock_inventory.call_args.args[0] == "hoodie v23"


def test_is_it_good_for_winter_reaches_live_lookup():
    mock_inventory = _run_query("Is Hoodie V23 good for winter?")
    mock_inventory.assert_called_once()


def test_what_do_you_know_about_x_reaches_live_lookup():
    mock_inventory = _run_query("What do you know about Premium Hoodie V23?")
    mock_inventory.assert_called_once()


def test_exact_product_name_is_preserved_not_collapsed_to_bare_category():
    """'Premium Hoodie V23' must extract with its qualifier intact - not
    just 'hoodie v23', which could ambiguously match multiple products
    (Premium/Signature/Essential Hoodie V23 all exist in this catalog)."""
    mock_inventory = _run_query("Is Premium Hoodie V23 durable?")
    assert mock_inventory.call_args.args[0] == "premium hoodie v23"
    assert mock_inventory.call_args.args[0] != "hoodie v23"


def test_ambiguous_bare_category_plus_version_asks_for_clarification():
    """'Hoodie V23' alone (no distinguishing prefix) genuinely could match
    Premium/Signature/Essential Hoodie V23 - the live tool's own ambiguous
    handling must be what surfaces the clarification, not a guess."""
    from src.agent.customer_success_agent import _enforce_no_ambiguous_product_claim
    ambiguous_result = {
        "success": True, "ambiguous": True,
        "matches": ["Premium Hoodie V23", "Signature Hoodie V23"],
        "message": "I found a few products matching 'hoodie v23': Premium Hoodie V23, Signature Hoodie V23. Which one did you mean?",
    }
    structured = {"reply_body": "The Hoodie V23 is a great choice!", "intent": "product_inquiry"}
    result = _enforce_no_ambiguous_product_claim(structured, ambiguous_result)
    assert "which one" in result["reply_body"].lower()
    assert "premium hoodie v23" in result["reply_body"].lower()
    assert "signature hoodie v23" in result["reply_body"].lower()


# ── Product matching precision at the Shopify-client level ─────────────────

def test_premium_hoodie_v23_title_search_does_not_match_signature_hoodie_v23():
    """Direct regression guard for Part 7 at the actual matching layer."""
    tools_client = ShopifyClient  # noqa: F841 - imported for clarity/traceability

    async def _run():
        client = ShopifyClient("test-shop.myshopify.com", "tok")
        products = [
            {"id": 1, "title": "Premium Hoodie V23", "status": "active", "variants": []},
            {"id": 2, "title": "Signature Hoodie V23", "status": "active", "variants": []},
        ]
        with patch.object(ShopifyClient, "list_active_products", new=AsyncMock(return_value=products)):
            return await client.find_products_by_title("premium hoodie v23")

    result = run(_run())
    titles = [p["title"] for p in result["products"]]
    assert titles == ["Premium Hoodie V23"]
    assert "Signature Hoodie V23" not in titles


# ═══════════════════════════════════════════════════════════════════════════
# PART 2/4/5/6 — HARD PRODUCT-GROUNDING CONTRACT
# ═══════════════════════════════════════════════════════════════════════════

def test_get_inventory_status_returns_real_description_and_price():
    """Root cause of the 'Pima Cotton' hallucination: get_inventory_status
    never returned the product's actual description or price even when
    Shopify data was already being fetched - the model had nothing
    authoritative to answer material/price questions with."""
    tools = V3Tools()
    product = {
        "id": 1, "title": "Premium Hoodie V23", "status": "active", "handle": "premium-hoodie-v23",
        "body_html": "<p>A high-end hoodie made from Organic Cotton. Fit: cropped.</p>",
        "variants": [{"title": "M", "price": "95.00", "inventory_quantity": 5, "inventory_management": "shopify"}],
        "options": [],
    }
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [product], "count": 1})):
        result = run(tools.get_inventory_status("premium hoodie v23", shop_domain="test-shop.myshopify.com", access_token="tok"))

    assert result["description"] == "A high-end hoodie made from Organic Cotton. Fit: cropped."
    assert result["price"] == "95.00"


def test_get_inventory_status_description_is_none_not_fabricated_when_absent():
    tools = V3Tools()
    product = {
        "id": 1, "title": "Basic Tee", "status": "active", "handle": "basic-tee", "body_html": None,
        "variants": [{"title": "M", "price": "20.00", "inventory_quantity": 5, "inventory_management": "shopify"}],
        "options": [],
    }
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [product], "count": 1})):
        result = run(tools.get_inventory_status("basic tee", shop_domain="test-shop.myshopify.com", access_token="tok"))

    assert result["description"] is None


def _tool_context_for_inventory(inv_result):
    """Runs the real tool_context-building path (via a live get_inventory_status
    mock feeding the agent) and returns the constructed system prompt."""
    captured = {}

    async def _capture_completion(*, messages, **kwargs):
        captured["messages"] = messages
        return _fake_ai_response("Here's what I found."), "p", "m", \
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 1, "attempts": 1}

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=_capture_completion)), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))), \
         patch("src.agent.customer_success_agent.v3_tools.get_inventory_status", new=AsyncMock(return_value=inv_result)):
        run(customer_success_agent.process_customer_query(
            query="Is Premium Hoodie V23 soft and durable?",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "email"},
            tenant_id="tenant-1", store_id="brand-1", ticket_id="ticket-1",
        ))
    return next(m["content"] for m in captured["messages"] if m["role"] == "system")


def test_live_shopify_data_is_marked_authoritative_over_rag_in_the_prompt():
    inv_result = {
        "success": True, "product": "Premium Hoodie V23",
        "description": "A high-end hoodie made from Organic Cotton. Fit: cropped.",
        "price": "95.00", "message": "Yes, Premium Hoodie V23 is in stock.", "variants": [],
    }
    prompt = _tool_context_for_inventory(inv_result)
    lowered = prompt.lower()
    assert "authoritative source for this product" in lowered
    assert "organic cotton" in lowered
    assert "95.00" in prompt
    assert "overrides knowledge base" in lowered


def test_missing_attributes_are_flagged_as_unverified_not_invented():
    inv_result = {
        "success": True, "product": "Premium Hoodie V23",
        "description": "A high-end hoodie made from Organic Cotton. Fit: cropped.",
        "price": "95.00", "message": "Yes, Premium Hoodie V23 is in stock.", "variants": [],
    }
    prompt = _tool_context_for_inventory(inv_result)
    lowered = prompt.lower()
    assert "say you don't have verified information" in lowered
    assert "pima cotton" not in lowered  # never fabricated anywhere in the constructed prompt
    assert "guessing or inferring it from the material name" in lowered


def test_no_description_available_does_not_invent_a_placeholder():
    inv_result = {
        "success": True, "product": "Basic Tee", "description": None, "price": "20.00",
        "message": "Yes, Basic Tee is in stock.", "variants": [],
    }
    prompt = _tool_context_for_inventory(inv_result)
    assert "LIVE Shopify description:" not in prompt
    assert "LIVE Shopify price: 20.00" in prompt


# ═══════════════════════════════════════════════════════════════════════════
# PART 9 — SAME-THREAD BEHAVIOR
# ═══════════════════════════════════════════════════════════════════════════

def test_same_thread_reply_now_reaches_ai_generation_instead_of_silently_stopping():
    """Previously: a reply in the same Gmail thread was appended to the
    ticket and the function returned immediately - no AI response was ever
    generated for it. Now it continues through the same pipeline as a new
    message, reusing the existing ticket rather than creating a new one."""
    proc = UnifiedMessageProcessor()
    existing_ticket = {
        "id": "existing-1", "gmail_thread_id": "thread-abc", "status": "open",
        "messages": [{"from": "customer@example.com", "body": "first message", "direction": "inbound"}],
    }
    captured = {}

    async def _capture_response(**kwargs):
        captured.update(kwargs)
        return _ai_result()

    def fake_select(table, params=None):
        if table == "tickets" and params and params.get("gmail_thread_id"):
            return [existing_ticket]
        if table == "tenants":
            return [{"id": "tenant-1"}]
        return []

    p_create = patch("src.workers.message_processor.supabase_service.create_ticket", new=AsyncMock())
    p_record_created = patch("src.services.plan_service.record_ticket_created")
    mocks = [
        patch("src.workers.message_processor.supabase_select", side_effect=fake_select),
        patch("src.workers.message_processor.supabase_update"),
        p_create,
        patch("src.workers.message_processor.supabase_service.get_system_settings", new=AsyncMock(return_value={"ai_mode": "active", "confidence_threshold": 0.65})),
        patch("src.workers.message_processor.supabase_service.get_or_create_customer", new=AsyncMock(return_value={"id": "customer-1", "email": "customer@example.com", "name": "Jane"})),
        patch("src.services.plan_service.record_email_processed"),
        patch("src.services.plan_service.can_process_ticket", return_value={"allowed": True}),
        p_record_created,
        patch("src.services.plan_service.check_limit", return_value={"allowed": True}),
        patch("src.services.plan_service.record_ai_reply_event"),
        patch("src.workers.message_processor.customer_success_agent.generate_channel_appropriate_response", new=_capture_response),
        patch("src.workers.message_processor.brand_message_processor._log_conversation", new=AsyncMock()),
    ]
    started = [m.start() for m in mocks]
    mock_create, mock_record_created = started[2], started[7]
    try:
        run(proc.process_message("email_incoming", {
            "channel": "email", "content": "second message", "customer_email": "customer@example.com",
            "customer_name": "Jane", "subject": "Hi", "store_id": "brand-1", "gmail_thread_id": "thread-abc",
        }))
    finally:
        for m in mocks:
            m.stop()

    # AI generation actually ran for the thread continuation.
    assert captured.get("query") == "second message"
    assert captured.get("ticket_id") == "existing-1"
    # No duplicate ticket was created for what's really a continuation.
    mock_create.assert_not_called()
    # No double-counting of an already-counted ticket.
    mock_record_created.assert_not_called()


def test_new_thread_still_creates_a_new_ticket_and_counts_it():
    """Control case: a genuinely new conversation (no thread match) is
    unaffected by the STAGE 1.5 change."""
    proc = UnifiedMessageProcessor()
    captured = {}

    async def _capture_response(**kwargs):
        captured.update(kwargs)
        return _ai_result()

    def fake_select(table, params=None):
        if table == "tickets" and params and params.get("gmail_thread_id"):
            return []  # no thread match
        if table == "brands":
            return [{"id": "brand-1", "tenant_id": "tenant-1"}]
        if table == "tenants":
            return [{"id": "tenant-1"}]
        return []

    p_create = patch("src.workers.message_processor.supabase_service.create_ticket", new=AsyncMock(return_value={"id": "new-ticket-1", "messages": []}))
    p_record_created = patch("src.services.plan_service.record_ticket_created")
    mocks = [
        patch("src.workers.message_processor.supabase_select", side_effect=fake_select),
        patch("src.workers.message_processor.supabase_update"),
        p_create,
        patch("src.workers.message_processor.supabase_service.get_system_settings", new=AsyncMock(return_value={"ai_mode": "active", "confidence_threshold": 0.65})),
        patch("src.workers.message_processor.supabase_service.get_or_create_customer", new=AsyncMock(return_value={"id": "customer-1", "email": "customer@example.com", "name": "Jane"})),
        patch("src.services.plan_service.record_email_processed"),
        patch("src.services.plan_service.can_process_ticket", return_value={"allowed": True}),
        p_record_created,
        patch("src.services.plan_service.check_limit", return_value={"allowed": True}),
        patch("src.services.plan_service.record_ai_reply_event"),
        patch("src.workers.message_processor.customer_success_agent.generate_channel_appropriate_response", new=_capture_response),
        patch("src.workers.message_processor.brand_message_processor._log_conversation", new=AsyncMock()),
    ]
    started = [m.start() for m in mocks]
    mock_create, mock_record_created = started[2], started[7]
    try:
        run(proc.process_message("email_incoming", {
            "channel": "email", "content": "brand new message", "customer_email": "customer@example.com",
            "customer_name": "Jane", "subject": "New topic", "store_id": "brand-1", "gmail_thread_id": "thread-xyz",
        }))
    finally:
        for m in mocks:
            m.stop()

    mock_create.assert_called_once()
    mock_record_created.assert_called_once_with("tenant-1")
    assert captured.get("ticket_id") == "new-ticket-1"


def test_stage9_finalization_update_never_overwrites_accumulated_messages():
    """STAGE 9's ticket_payload always rebuilds "messages" as a fresh
    single-item array containing only the current inbound message (see
    ticket_payload construction). For a thread continuation, STAGE 1.5
    already appended this message onto the ticket's real accumulated
    history - if STAGE 9's finalization update ever included "messages" in
    its update_fields, every single reply would silently wipe the ticket's
    history back down to one message, defeating both current-ticket history
    and cross-ticket memory the moment either is read back after a second
    reply."""
    proc = UnifiedMessageProcessor()
    existing_ticket = {
        "id": "existing-1", "gmail_thread_id": "thread-abc", "status": "open",
        "messages": [
            {"from": "customer@example.com", "body": "first message", "direction": "inbound"},
            {"from": "support@example.com", "body": "first reply", "direction": "outbound"},
        ],
    }

    async def _capture_response(**kwargs):
        return _ai_result()

    def fake_select(table, params=None):
        if table == "tickets" and params and params.get("gmail_thread_id"):
            return [existing_ticket]
        if table == "tenants":
            return [{"id": "tenant-1"}]
        return []

    update_calls = []

    def fake_update(table, match, fields):
        update_calls.append((table, match, fields))

    mocks = [
        patch("src.workers.message_processor.supabase_select", side_effect=fake_select),
        patch("src.workers.message_processor.supabase_update", side_effect=fake_update),
        patch("src.workers.message_processor.supabase_service.create_ticket", new=AsyncMock()),
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
            "channel": "email", "content": "second message", "customer_email": "customer@example.com",
            "customer_name": "Jane", "subject": "Hi", "store_id": "brand-1", "gmail_thread_id": "thread-abc",
        }))
    finally:
        for m in mocks:
            m.stop()

    # STAGE 9's finalization update (the one keyed on ticket_payload's
    # "status" field) must never include "messages".
    finalization_calls = [c for c in update_calls if c[0] == "tickets" and "status" in c[2]]
    assert finalization_calls, "expected a STAGE 9 finalization update to have happened"
    for _, _, fields in finalization_calls:
        assert "messages" not in fields


# ═══════════════════════════════════════════════════════════════════════════
# PART 10 (22-25) — POLICY / NON-PRODUCT REGRESSION (new fallback must not
# fire for these, exactly as the narrower "what is X" guard already didn't)
# ═══════════════════════════════════════════════════════════════════════════

def test_return_policy_question_still_does_not_trigger_product_lookup():
    mock_inventory = _run_query("What is your return policy?")
    mock_inventory.assert_not_called()


def test_order_status_question_still_does_not_trigger_product_lookup():
    mock_inventory = _run_query("What is happening with my order?")
    mock_inventory.assert_not_called()


def test_shipping_policy_question_does_not_trigger_product_lookup():
    """No category word present at all - safe even with the new general
    fallback active."""
    mock_inventory = _run_query("How long does shipping take?")
    mock_inventory.assert_not_called()


def test_recommendation_query_is_unaffected_by_the_new_general_fallback():
    """'similar' keyword already routes through the recommendation path -
    the new general product-mention fallback must not also fire and
    duplicate/interfere with it."""
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(return_value=(_fake_ai_response("Here's what I found!"), "test_provider", "test_model", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}))), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))), \
         patch("src.agent.customer_success_agent.v3_tools.get_inventory_status", new=AsyncMock()) as mock_inventory, \
         patch("src.agent.customer_success_agent.v3_tools.get_product_recommendations", new=AsyncMock(return_value={"success": True, "no_candidates": True, "message": "none"})) as mock_rec:
        run(customer_success_agent.process_customer_query(
            query="Show me something similar to the Premium Hoodie V23.",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "email"},
            tenant_id="tenant-1", store_id=None, ticket_id="ticket-1",
        ))

    mock_rec.assert_called_once()
    mock_inventory.assert_not_called()
