"""
"What products do you sell?" must answer from live Shopify data (real
titles from ShopifyClient.list_active_products, already used by
find_products_by_title/get_product_recommendations), never RAG/embeddings -
that generic catalog question has no product name for RAG to search on and
previously fell through to the fuzzy vector search (or a 500), which is
why Test Luna showed "Could not run the test right now."

v3_tools.list_catalog() is the only new code: reuses the existing Shopify
product-listing call, adds no new Shopify API surface, no Supabase catalog
duplication, no new agent architecture. Wired into
customer_success_agent.py as one more keyword-gated tool result (same
pattern as get_inventory_status), feeding the same tool_context the model
is already instructed to treat as authoritative over RAG.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.tools import v3_tools  # noqa: E402


@pytest.mark.asyncio
async def test_list_catalog_returns_real_product_titles():
    products = [{"title": "Essential Hoodie"}, {"title": "Winter Parka"}]
    with patch("src.services.shopify_service.ShopifyClient.list_active_products", new=AsyncMock(return_value=products)):
        result = await v3_tools.list_catalog(shop_domain="shop.myshopify.com", access_token="tok")

    assert result["success"] is True
    assert result["titles"] == ["Essential Hoodie", "Winter Parka"]
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_list_catalog_never_calls_embeddings_or_rag():
    """Regression for reliability requirement: a product-list question must
    not depend on an embedding provider at all."""
    products = [{"title": "Essential Hoodie"}]
    with patch("src.services.shopify_service.ShopifyClient.list_active_products", new=AsyncMock(return_value=products)), \
         patch("src.services.brand_knowledge_service.brand_knowledge_service.get_brand_context", new=AsyncMock(side_effect=AssertionError("RAG must not be called"))):
        result = await v3_tools.list_catalog(shop_domain="shop.myshopify.com", access_token="tok")

    assert result["success"] is True


@pytest.mark.asyncio
async def test_list_catalog_without_shopify_connected_gives_a_useful_message_not_a_crash():
    result = await v3_tools.list_catalog(shop_domain=None, access_token=None)
    assert result["success"] is False
    assert "catalog" in result["message"].lower()


@pytest.mark.asyncio
async def test_list_catalog_empty_store_does_not_hallucinate_products():
    """A store with zero active products is a valid, successfully-fetched
    empty result, not a Shopify failure - conflating the two would make a
    real Shopify outage indistinguishable from an honest "nothing for sale
    right now" answer. Either way, titles must never be invented."""
    with patch("src.services.shopify_service.ShopifyClient.list_active_products", new=AsyncMock(return_value=[])):
        result = await v3_tools.list_catalog(shop_domain="shop.myshopify.com", access_token="tok")

    assert result["success"] is True
    assert result["titles"] == []
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_list_catalog_shopify_failure_gives_safe_fallback_not_a_hallucination():
    with patch("src.services.shopify_service.ShopifyClient.list_active_products", new=AsyncMock(side_effect=Exception("Shopify down"))):
        result = await v3_tools.list_catalog(shop_domain="shop.myshopify.com", access_token="tok")

    assert result["success"] is False
    assert "team member" in result["message"].lower()


# ── Routing keyword sanity: the same list used in customer_success_agent.py ──

_CATALOG_KEYWORDS = [
    "what products do you sell", "what do you sell", "what products are available",
    "what products do you have", "what do you have available", "what do you carry",
    "what items do you sell", "what's in your store", "what is in your store",
    "show me your products", "list your products", "what products do you offer",
]


def test_catalog_keywords_match_the_reported_test_question():
    assert any(kw in "what products do you sell?" for kw in _CATALOG_KEYWORDS)


def test_catalog_keywords_do_not_match_a_specific_product_question():
    """Existing get_inventory_status routing for a NAMED product must be
    unaffected - "how much is the essential hoodie" should never also
    trigger the generic catalog gate."""
    q = "how much is the essential hoodie"
    assert not any(kw in q for kw in _CATALOG_KEYWORDS)


def test_catalog_keywords_do_not_match_a_policy_question():
    """Existing RAG/Knowledge Base routing for merchant policy questions
    must be unaffected."""
    q = "what is your return policy"
    assert not any(kw in q for kw in _CATALOG_KEYWORDS)


def test_catalog_keywords_do_not_match_an_order_question():
    q = "can i cancel order #1005"
    assert not any(kw in q for kw in _CATALOG_KEYWORDS)
