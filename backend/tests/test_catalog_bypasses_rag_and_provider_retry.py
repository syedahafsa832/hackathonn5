"""
Full-agent proof for the reported "Test Luna: Could not run the test right
now" bug on "What products do you sell?".

Root cause (see ai_provider_manager.py's _client_for): the chat-completion
OpenAI client was constructed with max_retries=1, unlike the embeddings
client next to it (deliberately max_retries=0, "our own key rotation IS
the retry strategy"). A single slow/rate-limited provider could burn up to
2x its 15s timeout via the SDK's own internal retry before this module's
own provider-rotation loop ever reached the next configured key - observed
live as "Retrying request to /chat/completions" in logs, pushing the whole
request past the frontend's 35s timeout even though RAG retrieval and the
Shopify catalog lookup had both already succeeded by then.

Also confirms the routing fix: a detected catalog question
(customer_success_agent.py) now skips the RAG/embedding call entirely
(moved earlier, before brand_knowledge_service.get_brand_context), so it
never depends on an embedding provider at all - not just "tolerates its
failure" as before.
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
from src.agent.customer_success_agent import customer_success_agent  # noqa: E402
from src.services.ai_provider_manager import AIProviderManager  # noqa: E402


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
    msg.content = json.dumps({"intent": "product_inquiry", "reply_body": reply_body, "risk_level": "low"})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


BRAND_ROW = {
    "id": "brand-1", "name": "Test Brand", "shopify_connected": True,
    "shopify_domain": "shop.myshopify.com", "shopify_access_token": "encrypted-token",
}


def _run_catalog_query(products):
    captured = {}
    rag_calls = []

    async def _capture_completion(*, messages, **kwargs):
        captured["messages"] = messages
        return _fake_ai_response("We currently sell the Essential Hoodie and the Winter Parka."), \
            "test_provider", "test_model", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}

    async def _fail_if_rag_called(*args, **kwargs):
        rag_calls.append((args, kwargs))
        return "SHOULD NOT BE CALLED"

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=_capture_completion)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(side_effect=_fail_if_rag_called)), \
         patch("src.lib.supabase_client.supabase_select", return_value=[BRAND_ROW]), \
         patch("src.services.shopify_service.decrypt_token", return_value="real-token"), \
         patch("src.services.shopify_service.ShopifyClient.list_active_products", new=AsyncMock(return_value=products)), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))):
        result = run(customer_success_agent.process_customer_query(
            query="What products do you sell?",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
        ))
    return result, captured, rag_calls


# ── 1/2. Routes to Shopify catalog, RAG/embeddings never called ────────────

def test_catalog_question_never_calls_rag_or_requires_embeddings():
    products = [{"title": "Essential Hoodie"}, {"title": "Winter Parka"}]
    _, _, rag_calls = _run_catalog_query(products)
    assert rag_calls == [], "get_brand_context (RAG/embeddings) must not be called for a catalog question"


# ── 3/6. Real Shopify product names reach the final prompt ─────────────────

def test_catalog_titles_reach_the_final_model_prompt():
    products = [{"title": "Essential Hoodie"}, {"title": "Winter Parka"}]
    _, captured, _ = _run_catalog_query(products)
    system_content = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "Essential Hoodie" in system_content
    assert "Winter Parka" in system_content
    assert "LIVE SHOPIFY CATALOG" in system_content


# ── 7. Test Luna / agent returns success for the catalog question ──────────

def test_catalog_question_produces_a_successful_non_escalated_reply():
    products = [{"title": "Essential Hoodie"}]
    result, _, _ = _run_catalog_query(products)
    assert result.get("reply_body")
    assert result.get("provider_outage") is not True


# ── 4. Empty Shopify catalog does not crash, does not hallucinate ─────────

def test_empty_catalog_does_not_crash_the_agent():
    """A successful Shopify call that legitimately returns zero active
    products is a valid empty answer, not a failure - tools.list_catalog()
    only takes the failure path on a real exception/missing credentials
    (see its own docstring). It must render as a live "0 active products"
    catalog line, not the CATALOG LOOKUP failure message, and the customer
    must still get a reply either way."""
    result, captured, _ = _run_catalog_query([])
    system_content = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "LIVE SHOPIFY CATALOG (0 active products)" in system_content
    assert "CATALOG LOOKUP" not in system_content
    assert result.get("reply_body")  # still produces a reply, never crashes


# ── Provider client construction: the actual root cause fix ────────────────

def test_chat_completion_client_does_not_use_sdk_internal_retries():
    """The exact fix: max_retries=0 so a slow/failing provider fails fast
    and hands off to the next configured key instead of doubling its own
    timeout via the SDK's internal retry - matches the embeddings client's
    own already-established policy."""
    mgr = AIProviderManager()
    if not mgr.has_providers:
        import pytest
        pytest.skip("no providers configured in this environment")
    provider = mgr._providers[0]
    client = mgr._client_for(provider)
    assert client.max_retries == 0
