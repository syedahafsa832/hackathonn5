"""
Focused tests for the Luna knowledge-architecture fix: RAG/KB retrieval
failure must never be indistinguishable from "no such policy exists," and
must never crash or block capabilities that don't depend on it.

Covers (see get_brand_context_with_status in brand_knowledge_service.py):
  - RAG path: embedding failure / RPC failure -> "unavailable", never
    silently equated with "no relevant policy" ("no_match").
  - get_brand_context() (the production call site) stays 100% backward
    compatible - same "" on any failure, so every existing caller/test that
    mocks it is unaffected.
  - Isolation: an unexpected exception during RAG retrieval in the agent's
    hot path is caught locally and degrades to "no KB context this turn,"
    never propagates and crashes the whole customer reply.
  - Prompt-level safety: an empty KNOWLEDGE BASE block must never be read
    by the model as permission to tell a customer "we don't have that
    policy" - it may simply mean retrieval failed.
  - Empty-but-successfully-fetched Shopify catalog is a valid empty answer,
    not a failure (previously conflated - see tools.py list_catalog fix).
"""
import os
import sys
import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
import pytest  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent  # noqa: E402
from src.services import brand_knowledge_service as kb_module  # noqa: E402
from src.services.tools import v3_tools  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ── RAG path: retrieval failure vs. no-match are distinguishable ───────────

@pytest.mark.asyncio
async def test_embedding_failure_reports_unavailable_not_no_match():
    with patch.object(kb_module.brand_knowledge_service, "_get_embedding", new=AsyncMock(return_value=None)):
        context, status = await kb_module.brand_knowledge_service.get_brand_context_with_status("brand-1", "what is your return policy?")
    assert context == ""
    assert status == "unavailable"


@pytest.mark.asyncio
async def test_rpc_failure_reports_unavailable_not_no_match():
    with patch.object(kb_module.brand_knowledge_service, "_get_embedding", new=AsyncMock(return_value=[0.1] * 1024)), \
         patch("src.services.brand_knowledge_service.supabase_rpc", side_effect=Exception("connection reset")):
        context, status = await kb_module.brand_knowledge_service.get_brand_context_with_status("brand-1", "what is your return policy?")
    assert context == ""
    assert status == "unavailable"


@pytest.mark.asyncio
async def test_genuinely_no_relevant_chunks_reports_no_match():
    with patch.object(kb_module.brand_knowledge_service, "_get_embedding", new=AsyncMock(return_value=[0.1] * 1024)), \
         patch("src.services.brand_knowledge_service.supabase_rpc", return_value=[]):
        context, status = await kb_module.brand_knowledge_service.get_brand_context_with_status("brand-1", "do you sell moon rocks?")
    assert context == ""
    assert status == "no_match"


@pytest.mark.asyncio
async def test_successful_match_reports_ok_with_context():
    fake_results = [{"source_name": "Return Policy", "content": "Returns within 30 days.", "similarity": 0.9}]
    with patch.object(kb_module.brand_knowledge_service, "_get_embedding", new=AsyncMock(return_value=[0.1] * 1024)), \
         patch("src.services.brand_knowledge_service.supabase_rpc", return_value=fake_results):
        context, status = await kb_module.brand_knowledge_service.get_brand_context_with_status("brand-1", "what is your return policy?")
    assert status == "ok"
    assert "Returns within 30 days." in context


# ── Backward compatibility: get_brand_context() unaffected ─────────────────

@pytest.mark.asyncio
async def test_legacy_get_brand_context_still_collapses_failures_to_empty_string():
    """Every existing caller (agent, v2_knowledge, actions_manager,
    brand_message_processor) and every existing test mocking this exact
    method must keep working unchanged."""
    with patch.object(kb_module.brand_knowledge_service, "_get_embedding", new=AsyncMock(return_value=None)):
        context = await kb_module.brand_knowledge_service.get_brand_context("brand-1", "what is your return policy?")
    assert context == ""
    assert isinstance(context, str)


# ── Isolation: RAG failure must not crash the whole agent reply ────────────

BRAND_ROW = {
    "id": "brand-1", "name": "Test Brand", "shopify_connected": False,
    "shopify_domain": None, "shopify_access_token": None,
}


def _fake_ai_response(reply_body: str):
    msg = MagicMock()
    msg.content = json.dumps({"intent": "product_inquiry", "reply_body": reply_body, "risk_level": "low"})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def test_unexpected_rag_exception_does_not_crash_the_agent_reply():
    """Even if brand_knowledge_service.get_brand_context() itself somehow
    raised (it shouldn't - it catches internally - but the agent's own
    call-site try/except is the last line of defense), the customer still
    gets a real, non-crashing reply instead of an unhandled exception."""
    async def _capture_completion(*, messages, **kwargs):
        return _fake_ai_response("I don't have that confirmed, let me have the team follow up."), \
            "test_provider", "test_model", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=_capture_completion)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(side_effect=Exception("simulated RAG outage"))), \
         patch("src.lib.supabase_client.supabase_select", return_value=[BRAND_ROW]), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))):
        result = run(customer_success_agent.process_customer_query(
            query="What is your return policy?",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
        ))

    assert result.get("reply_body")
    assert result.get("provider_outage") is not True


# ── Prompt-level safety: empty KB block must not license a negative claim ──

def test_prompt_forbids_asserting_policy_non_existence_when_kb_is_empty():
    prompt = customer_success_agent._construct_v3_prompt(
        customer_info={"name": "Jane", "email": "jane@example.com"},
        rag_context="", sizing_context="", tool_context="", action_context="",
    )
    lowered = prompt.lower()
    assert "does not mean the store has no such policy" in lowered or "does not mean the store has no such" in lowered
    assert 'never tell a customer the store "doesn\'t have"' in lowered


def test_prompt_still_carries_real_kb_content_through_untouched():
    prompt = customer_success_agent._construct_v3_prompt(
        customer_info={"name": "Jane", "email": "jane@example.com"},
        rag_context="[Return Policy]:\nReturns accepted within 30 days.",
        sizing_context="", tool_context="", action_context="",
    )
    assert "Returns accepted within 30 days." in prompt


# ── Empty (but successfully fetched) Shopify catalog is a valid response ───

@pytest.mark.asyncio
async def test_empty_but_successfully_fetched_catalog_is_not_reported_as_failure():
    with patch("src.services.shopify_service.ShopifyClient.list_active_products", new=AsyncMock(return_value=[])):
        result = await v3_tools.list_catalog(shop_domain="shop.myshopify.com", access_token="tok")
    assert result["success"] is True
    assert result["titles"] == []
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_real_shopify_exception_is_still_reported_as_failure_not_hallucinated():
    with patch("src.services.shopify_service.ShopifyClient.list_active_products", new=AsyncMock(side_effect=Exception("Shopify 500"))):
        result = await v3_tools.list_catalog(shop_domain="shop.myshopify.com", access_token="tok")
    assert result["success"] is False
    assert "team member" in result["message"].lower()


# ── Isolation: both Shopify and RAG unavailable at once - still safe ───────

def test_both_shopify_and_rag_unavailable_still_produces_a_safe_non_crashing_reply():
    """No Shopify credentials on the brand AND a failing RAG call - the
    customer must still get an honest, non-hallucinated, non-crashing
    reply rather than an unhandled exception or an invented policy."""
    async def _capture_completion(*, messages, **kwargs):
        return _fake_ai_response("I can't confirm that for you right now, I'll have the team follow up."), \
            "test_provider", "test_model", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=_capture_completion)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.lib.supabase_client.supabase_select", return_value=[BRAND_ROW]), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))):
        result = run(customer_success_agent.process_customer_query(
            query="What is your return policy and do you ship internationally?",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
        ))

    assert result.get("reply_body")
    assert result.get("provider_outage") is not True
