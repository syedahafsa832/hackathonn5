"""
Focused tests for "Fix Luna: Brand Correction + Secure Order Context + RAG
Resilience".

Two related fixes:

1. Brand-name correction (customer_success_agent.py): a customer naming the
   wrong store ("Hasha Clothing" instead of the real connected brand) gets a
   brief, natural correction - detected deterministically via
   _detect_store_name_mismatch(), never via an LLM guess, and NEVER
   conflated with identity verification. The connected brand's own
   Shopify credentials (_brand_shopify_domain/_brand_shopify_token,
   resolved from store_id) are the only ones ever used - the customer's
   wrong store name is never passed to any Shopify/tenant-resolution call,
   so it structurally cannot search another merchant or bypass verification.

2. RAG is no longer a hidden prerequisite for Shopify-answerable questions.
   The RAG fetch was moved to run AFTER Shopify/order/inventory/
   recommendation tool dispatch (previously it ran first), and now skips
   whenever any of those tools already produced a result (tool_results
   non-empty) - not just for the narrow catalog-keyword case handled by
   the prior fix. Deterministic cancel/refund/exchange eligibility already
   sources its own policy evidence independently via
   actions_manager.get_custom_policy_text() (return_actions_integration.py),
   so skipping the main rag_context fetch never weakens those decisions.
"""
import os
import sys
import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402
from src.agent.customer_success_agent import (  # noqa: E402
    customer_success_agent, _detect_store_name_mismatch,
)


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _fake_ai_response(reply_body: str):
    msg = MagicMock()
    msg.content = json.dumps({"intent": "order_status_inquiry", "reply_body": reply_body, "risk_level": "low"})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


REAL_BRAND_NAME = "Syedahafsa1983's Clothing Store"
BRAND_ROW = {
    "id": "brand-1", "name": REAL_BRAND_NAME, "shopify_connected": True,
    "shopify_domain": "tresolv.myshopify.com", "shopify_access_token": "encrypted-token",
}


def _run_query(query, order_status_result, customer_email="jane@example.com"):
    captured = {}

    async def _capture_completion(*, messages, **kwargs):
        captured["messages"] = messages
        return _fake_ai_response("Sure, here's what I can tell you."), \
            "test_provider", "test_model", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}

    shopify_calls = []

    async def _fake_get_order_status(order_id, shop_domain=None, access_token=None, customer_email=None):
        shopify_calls.append({"order_id": order_id, "shop_domain": shop_domain, "access_token": access_token, "customer_email": customer_email})
        return order_status_result

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=_capture_completion)), \
         patch("src.lib.supabase_client.supabase_select", return_value=[BRAND_ROW]), \
         patch("src.services.shopify_service.decrypt_token", return_value="real-token"), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(side_effect=_fake_get_order_status)), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))):
        result = run(customer_success_agent.process_customer_query(
            query=query,
            customer_info={"name": "Jane", "email": customer_email, "channel": "chat"},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
        ))
    return result, captured, shopify_calls


# ── 1-2. Brand-name mismatch is detected and corrected naturally ───────────

def test_detect_store_name_mismatch_flags_the_wrong_name():
    mentioned = _detect_store_name_mismatch("tell me about hasha clothing store order #1002", REAL_BRAND_NAME)
    assert mentioned is not None
    assert "hasha" in mentioned.lower()


def test_detect_store_name_mismatch_ignores_a_matching_name():
    assert _detect_store_name_mismatch("hi Syedahafsa1983's clothing store, order #1002 please", REAL_BRAND_NAME) is None


def test_detect_store_name_mismatch_ignores_a_message_with_no_store_name():
    assert _detect_store_name_mismatch("Oh sorry, I was wrong.", REAL_BRAND_NAME) is None
    assert _detect_store_name_mismatch("where is my order #1002", REAL_BRAND_NAME) is None


def test_detect_store_name_mismatch_ignores_ordinary_your_store_phrasing():
    """"your store"/"this store" is how customers overwhelmingly refer to
    THIS store generically - must never be misread as naming a different
    one, even when an unrelated product name appears earlier in the same
    sentence."""
    assert _detect_store_name_mismatch("is the QA Test Tee available in your store", REAL_BRAND_NAME) is None
    assert _detect_store_name_mismatch("is the QA Test Tee in stock at your store?", REAL_BRAND_NAME) is None
    assert _detect_store_name_mismatch("do you have this in your store", REAL_BRAND_NAME) is None
    assert _detect_store_name_mismatch("can you check your store for the winter parka", REAL_BRAND_NAME) is None
    assert _detect_store_name_mismatch("what do you have in your shop", REAL_BRAND_NAME) is None


def test_wrong_store_name_produces_a_correction_instruction_in_the_prompt():
    order_result = {"success": True, "order_number": "1002", "status": "processing"}
    result, captured, _ = _run_query("tell me about hasha clothing store order #1002", order_result)
    system_content = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "BRAND NAME CORRECTION" in system_content
    assert "hasha" in system_content.lower()
    assert REAL_BRAND_NAME in system_content
    assert result.get("reply_body")


# ── 3-4. Brand correction never changes which Shopify store is queried ─────

def test_wrong_store_name_never_changes_which_shopify_credentials_are_used():
    order_result = {"success": True, "order_number": "1002", "status": "processing"}
    _, _, shopify_calls = _run_query("tell me about hasha clothing store order #1002", order_result)
    assert len(shopify_calls) == 1
    # Always the connected brand's own resolved credentials - never derived
    # from the customer's "hasha clothing store" text.
    assert shopify_calls[0]["shop_domain"] == "tresolv.myshopify.com"
    assert shopify_calls[0]["access_token"] == "real-token"


def test_wrong_store_name_does_not_change_the_order_id_looked_up():
    order_result = {"success": True, "order_number": "1002", "status": "processing"}
    _, _, shopify_calls = _run_query("tell me about hasha clothing store order #1002", order_result)
    assert shopify_calls[0]["order_id"] == "1002"


# ── 5-6. Order #1002 still goes through normal identity verification ──────

def test_brand_correction_coexists_with_a_passed_identity_check():
    """Identity verified (email matches) - correction + real order info,
    never an identity-verification-style refusal."""
    order_result = {"success": True, "order_number": "1002", "status": "processing"}
    result, captured, _ = _run_query("tell me about hasha clothing store order #1002", order_result)
    system_content = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "BRAND NAME CORRECTION" in system_content
    assert "ORDER IDENTITY UNVERIFIED" not in system_content
    assert result.get("reply_body")


def test_brand_correction_never_bypasses_a_failed_identity_check():
    """Identity NOT verified (ownership_mismatch) - correction may still be
    present, but the security-critical ORDER IDENTITY UNVERIFIED
    instruction (never reveal order details) must still fire exactly as
    it would without any store-name mismatch."""
    order_result = {"error": "Order #1002 not found.", "order_number": "1002", "ownership_mismatch": True}
    result, captured, _ = _run_query("tell me about hasha clothing store order #1002", order_result)
    system_content = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "BRAND NAME CORRECTION" in system_content
    assert "ORDER IDENTITY UNVERIFIED" in system_content
    assert "Do NOT reveal any details about this order" in system_content
    assert result.get("reply_body")


def test_correcting_the_store_name_is_not_treated_as_identity_verification_itself():
    """The correction instruction text must be clearly wording-only and
    must not itself instruct the model to treat the mismatch as a security
    problem."""
    order_result = {"success": True, "order_number": "1002", "status": "processing"}
    _, captured, _ = _run_query("tell me about hasha clothing store order #1002", order_result)
    system_content = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    correction_block = system_content[system_content.index("BRAND NAME CORRECTION"):]
    correction_block = correction_block[:correction_block.index("\n\n") if "\n\n" in correction_block else len(correction_block)]
    assert "not a security concern" in system_content.lower()
    assert "do not treat it as identity verification failing".lower() in system_content.lower() or "do not treat it as identity verification failing" in system_content


# ── 7-13. Shopify-answerable questions bypass RAG ───────────────────────────

def _run_shopify_question(query, tool_patch_target, tool_return_value, extra_patches=None):
    captured = {}
    rag_calls = []

    async def _capture_completion(*, messages, **kwargs):
        captured["messages"] = messages
        return _fake_ai_response("Here you go."), "test_provider", "test_model", \
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}

    async def _fail_if_rag_called(*args, **kwargs):
        rag_calls.append((args, kwargs))
        return "SHOULD NOT BE CALLED"

    patches = [
        patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True),
        patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=_capture_completion)),
        patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(side_effect=_fail_if_rag_called)),
        patch("src.lib.supabase_client.supabase_select", return_value=[BRAND_ROW]),
        patch("src.services.shopify_service.decrypt_token", return_value="real-token"),
        patch(tool_patch_target, new=AsyncMock(return_value=tool_return_value)),
        patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))),
    ]
    if extra_patches:
        patches.extend(extra_patches)

    from contextlib import ExitStack
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = run(customer_success_agent.process_customer_query(
            query=query,
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
        ))
    return result, captured, rag_calls


def test_product_price_question_bypasses_rag():
    result, _, rag_calls = _run_shopify_question(
        "how much is the QA Test Tee?",
        "src.agent.customer_success_agent.v3_tools.get_inventory_status",
        {"success": True, "message": "The QA Test Tee is $25.00 and in stock.", "product_name": "QA Test Tee"},
    )
    assert rag_calls == []
    assert result.get("reply_body")


def test_inventory_question_bypasses_rag():
    result, _, rag_calls = _run_shopify_question(
        "is the QA Test Tee in stock?",
        "src.agent.customer_success_agent.v3_tools.get_inventory_status",
        {"success": True, "message": "Yes, the QA Test Tee is in stock.", "product_name": "QA Test Tee"},
    )
    assert rag_calls == []
    assert result.get("reply_body")


def test_variant_question_bypasses_rag():
    result, _, rag_calls = _run_shopify_question(
        "what sizes does the QA Test Tee come in?",
        "src.agent.customer_success_agent.v3_tools.get_inventory_status",
        {"success": True, "message": "The QA Test Tee comes in S, M, L, XL.", "product_name": "QA Test Tee"},
    )
    assert rag_calls == []
    assert result.get("reply_body")


def test_order_status_question_bypasses_rag():
    result, _, rag_calls = _run_shopify_question(
        "where is my order #1005?",
        "src.agent.customer_success_agent.v3_tools.get_order_status",
        {"success": True, "order_number": "1005", "status": "shipped"},
    )
    assert rag_calls == []
    assert result.get("reply_body")


def test_cancellation_question_bypasses_the_generic_top_level_rag_fetch():
    """The main agent's own top-level rag_context fetch (used to ground the
    generic KNOWLEDGE BASE prompt section) must be skipped, since Shopify
    order data already answered this. return_actions_integration.py's
    deterministic cancellation-window check separately (and correctly)
    still sources its OWN policy text via actions_manager.get_custom_policy_text()
    - a targeted, necessary lookup, not the customer's raw question - so
    get_brand_context CAN still be called, just never with the original
    top-level query."""
    result, _, rag_calls = _run_shopify_question(
        "can I cancel order #1009?",
        "src.agent.customer_success_agent.v3_tools.get_order_status",
        {"success": True, "order_number": "1009", "status": "processing"},
    )
    top_level_calls = [c for c in rag_calls if c[1].get("query") == "can I cancel order #1009?"]
    assert top_level_calls == []
    assert result.get("reply_body")


def test_address_change_question_bypasses_rag():
    result, _, rag_calls = _run_shopify_question(
        "I need to change the address on order #1009.",
        "src.agent.customer_success_agent.v3_tools.get_order_status",
        {"success": True, "order_number": "1009", "status": "processing"},
    )
    assert rag_calls == []
    assert result.get("reply_body")


# ── 14-15. Shopify questions succeed when embeddings/RAG are unavailable ───

def test_shopify_question_succeeds_when_embeddings_are_unavailable():
    """Even if RAG were (incorrectly) invoked, an embedding outage must not
    take down a Shopify-answerable question - here it's never invoked at
    all, which is the stronger, correct guarantee."""
    result, _, rag_calls = _run_shopify_question(
        "where is my order #1005?",
        "src.agent.customer_success_agent.v3_tools.get_order_status",
        {"success": True, "order_number": "1005", "status": "shipped"},
    )
    assert rag_calls == []
    assert result.get("reply_body")
    assert result.get("provider_outage") is not True


def test_shopify_question_survives_rag_raising_an_exception():
    """A pure policy/general question with NO Shopify tool match still goes
    through RAG, and a raised exception there must be isolated (see the
    agent's own call-site try/except), never crash the whole reply."""
    captured = {}

    async def _capture_completion(*, messages, **kwargs):
        captured["messages"] = messages
        return _fake_ai_response("Here's our policy."), "test_provider", "test_model", \
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=_capture_completion)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(side_effect=Exception("embedding outage"))), \
         patch("src.lib.supabase_client.supabase_select", return_value=[BRAND_ROW]), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))):
        result = run(customer_success_agent.process_customer_query(
            query="What is your return policy?",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1", store_id="brand-1", ticket_id="ticket-1",
        ))
    assert result.get("reply_body")
    assert result.get("provider_outage") is not True


def test_sizing_policy_question_still_uses_kb_not_a_false_positive_inventory_match():
    """The new "sizes"/"come in" inventory keywords must not swallow a
    genuine policy question ("What's your sizing policy?" - PART 7's own
    example) - it should still go through normal KB/RAG handling."""
    captured = {}
    rag_calls = []

    async def _capture_completion(*, messages, **kwargs):
        captured["messages"] = messages
        return _fake_ai_response("Here's our sizing guidance."), "test_provider", "test_model", \
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}

    async def _fake_rag(brand_id, query, top_k=5):
        rag_calls.append(query)
        return "[Sizing Policy]:\nWe run true to size."

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=_capture_completion)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(side_effect=_fake_rag)), \
         patch("src.lib.supabase_client.supabase_select", return_value=[BRAND_ROW]), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))):
        result = run(customer_success_agent.process_customer_query(
            query="What's your sizing policy?",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1", store_id="brand-1", ticket_id="ticket-1",
        ))

    assert rag_calls == ["What's your sizing policy?"]
    assert result.get("reply_body")


# ── 16-17. Policy questions use KB retrieval, never fabricate on failure ───

def test_policy_question_uses_kb_retrieval():
    captured = {}
    rag_calls = []

    async def _capture_completion(*, messages, **kwargs):
        captured["messages"] = messages
        return _fake_ai_response("Here's our policy."), "test_provider", "test_model", \
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}

    async def _fake_rag(brand_id, query, top_k=5):
        rag_calls.append(query)
        return "[Return Policy]:\nReturns accepted within 30 days."

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=_capture_completion)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(side_effect=_fake_rag)), \
         patch("src.lib.supabase_client.supabase_select", return_value=[BRAND_ROW]), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))):
        result = run(customer_success_agent.process_customer_query(
            query="What is your return policy?",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1", store_id="brand-1", ticket_id="ticket-1",
        ))

    assert rag_calls == ["What is your return policy?"]
    system_content = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "Returns accepted within 30 days." in system_content
    assert result.get("reply_body")


def test_policy_question_does_not_fabricate_when_kb_retrieval_fails():
    captured = {}

    async def _capture_completion(*, messages, **kwargs):
        captured["messages"] = messages
        return _fake_ai_response("I don't have that confirmed, let me have the team follow up."), \
            "test_provider", "test_model", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=_capture_completion)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.lib.supabase_client.supabase_select", return_value=[BRAND_ROW]), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))):
        result = run(customer_success_agent.process_customer_query(
            query="What is your return policy?",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1", store_id="brand-1", ticket_id="ticket-1",
        ))

    system_content = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "does not mean the store has no such policy" in system_content.lower()
    assert result.get("reply_body")


# ── 18-19. Embedding failure never takes down unrelated functionality ──────

def test_embedding_failure_does_not_prevent_shopify_tools_from_working():
    result, _, rag_calls = _run_shopify_question(
        "where is my order #1005?",
        "src.agent.customer_success_agent.v3_tools.get_order_status",
        {"success": True, "order_number": "1005", "status": "shipped"},
    )
    assert rag_calls == []
    assert result.get("reply_body")


def test_embedding_failure_does_not_break_a_general_conversation_with_no_rag_need():
    """A generic greeting/small-talk message matches no Shopify keyword and
    is not a policy question either - RAG still gets attempted (current
    keyword-based routing has no "general chit-chat" detector to skip it
    outright), but a failure there must still degrade gracefully rather
    than crash the reply."""
    captured = {}

    async def _capture_completion(*, messages, **kwargs):
        captured["messages"] = messages
        return _fake_ai_response("Hi there! How can I help today?"), "test_provider", "test_model", \
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=_capture_completion)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(side_effect=Exception("embedding outage"))), \
         patch("src.lib.supabase_client.supabase_select", return_value=[BRAND_ROW]), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))):
        result = run(customer_success_agent.process_customer_query(
            query="hi there!",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1", store_id="brand-1", ticket_id="ticket-1",
        ))
    assert result.get("reply_body")
    assert result.get("provider_outage") is not True


# ── 23-24. Onboarding Test Luna: catalog path, no embeddings required ──────

def test_onboarding_catalog_question_uses_shopify_and_never_calls_rag():
    """Same production process_customer_query() path onboarding's
    /test-reply endpoint calls - "What products do you sell?" must reach
    real Shopify data (mocked here) and never touch RAG/embeddings."""
    result, captured, rag_calls = _run_shopify_question(
        "What products do you sell?",
        "src.agent.customer_success_agent.v3_tools.list_catalog",
        {"success": True, "titles": ["Essential Hoodie", "Winter Parka"], "count": 2},
    )
    assert rag_calls == []
    system_content = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "Essential Hoodie" in system_content
    assert "LIVE SHOPIFY CATALOG" in system_content
    assert result.get("reply_body")
    assert result.get("provider_outage") is not True
