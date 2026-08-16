"""
Category/need-based product discovery ("I need a hoodie for winter, what
would you suggest") — the gap that let the model invent products/materials
it never looked up. get_product_recommendations() requires resolving one
exact anchor product first (via "similar to X" phrasing); a plain need
statement has no anchor to extract, so it previously reached the LLM with
zero live product data at all.

discover_products_by_category() reuses the exact same live-Shopify-fetch
architecture (list_active_products/find_products_by_title) as the rest of
tools.py — no ML, no new fetch path. Never invents a product. Too many
matches with nothing to narrow by -> asks the customer to narrow down
rather than dumping the catalog or guessing.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from unittest.mock import MagicMock, PropertyMock  # noqa: E402
from src.services.tools import V3Tools  # noqa: E402
from src.services.shopify_service import ShopifyClient  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent  # noqa: E402


def run(coro):
    # See test_chat_widget_action_staging.py's run() for why: asyncio.get_
    # event_loop() can return an already-closed loop left behind by an
    # earlier @pytest.mark.asyncio test in the full suite.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _product(id_, title, product_type="Hoodie", price="50.00", body_html=None, quantity=5, inv_mgmt="shopify"):
    return {
        "id": id_, "title": title, "status": "active",
        "product_type": product_type, "handle": title.lower().replace(" ", "-"),
        "body_html": body_html,
        "variants": [{"title": "M", "price": price, "inventory_quantity": quantity, "inventory_management": inv_mgmt}],
    }


def test_few_matches_returns_real_grounded_products_with_description():
    products = [
        _product(1, "Essential Hoodie", body_html="<p>100% cotton fleece, relaxed fit.</p>"),
        _product(2, "Premium Hoodie", body_html=None),
    ]
    tools = V3Tools()
    with patch.object(ShopifyClient, "list_active_products", new=AsyncMock(return_value=products)):
        result = run(tools.discover_products_by_category("hoodie", shop_domain="test-shop.myshopify.com", access_token="tok"))

    assert result["success"] is True
    titles = [r["title"] for r in result["recommendations"]]
    assert titles == ["Essential Hoodie", "Premium Hoodie"]
    assert result["recommendations"][0]["description"] == "100% cotton fleece, relaxed fit."
    # No body_html at all -> None, never a fabricated description.
    assert result["recommendations"][1]["description"] is None
    assert result["recommendations"][0]["price"] == "50.00"


def test_too_many_matches_asks_for_clarification_instead_of_listing_or_guessing():
    """Real HafsaClothing-shaped scenario: 90 products all product_type='Hoodie'.
    Must not dump all 90, must not silently pick one — asks a real clarifying
    question (budget/style), matching the desired UX exactly."""
    products = [_product(i, f"Essential Hoodie V{i}") for i in range(1, 31)]
    tools = V3Tools()
    with patch.object(ShopifyClient, "list_active_products", new=AsyncMock(return_value=products)):
        result = run(tools.discover_products_by_category("hoodie", shop_domain="test-shop.myshopify.com", access_token="tok"))

    assert result["success"] is True
    assert result["needs_clarification"] is True
    assert result["match_count"] == 30
    assert "recommendations" not in result
    assert "budget" in result["message"].lower()


def test_no_matches_is_honest_not_a_hallucinated_substitute():
    tools = V3Tools()
    with patch.object(ShopifyClient, "list_active_products", new=AsyncMock(return_value=[])), \
         patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [], "count": 0})):
        result = run(tools.discover_products_by_category("parka", shop_domain="test-shop.myshopify.com", access_token="tok"))

    assert result["success"] is True
    assert result["no_candidates"] is True
    assert "parka" in result["message"].lower()


def test_falls_back_to_title_search_when_product_type_does_not_match():
    """Merchant's product_type field doesn't literally say 'jacket', but a
    product titled 'Winter Jacket' exists — title-word fallback finds it
    rather than reporting no_candidates prematurely."""
    products = [_product(1, "Sweater", product_type="Knitwear")]
    title_match = [_product(2, "Winter Jacket", product_type="Outerwear")]
    tools = V3Tools()
    with patch.object(ShopifyClient, "list_active_products", new=AsyncMock(return_value=products)), \
         patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": title_match, "count": 1})):
        result = run(tools.discover_products_by_category("jacket", shop_domain="test-shop.myshopify.com", access_token="tok"))

    assert result["success"] is True
    assert result["recommendations"][0]["title"] == "Winter Jacket"


def test_missing_credentials_fails_honestly_not_silently():
    tools = V3Tools()
    result = run(tools.discover_products_by_category("hoodie", shop_domain=None, access_token=None))
    assert result["success"] is False


# ── agent trigger: discovery queries reach the live tool, not just the LLM ──

def _fake_ai_response(reply_body: str):
    import json
    msg = MagicMock()
    msg.content = json.dumps({"intent": "product_inquiry", "reply_body": reply_body, "risk_level": "low"})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _run_query(message: str, discover_return=None):
    from unittest.mock import AsyncMock, patch
    discover_return = discover_return or {"success": True, "no_candidates": True, "category": "hoodie", "message": "none found"}
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(return_value=(_fake_ai_response("Here's what I found!"), "test_provider", "test_model", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}))), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))), \
         patch("src.agent.customer_success_agent.v3_tools.discover_products_by_category", new=AsyncMock(return_value=discover_return)) as mock_discover, \
         patch("src.agent.customer_success_agent.v3_tools.get_inventory_status", new=AsyncMock()) as mock_inventory:
        run(customer_success_agent.process_customer_query(
            query=message,
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "email"},
            tenant_id="tenant-1",
            store_id=None,
            ticket_id="ticket-1",
        ))
    return mock_discover, mock_inventory


def test_need_a_hoodie_what_would_you_suggest_reaches_the_live_tool():
    """The exact real-world phrasing that previously reached the LLM with zero
    live product data and produced an invented Merino Wool / V20 / V2
    recommendation - must now call discover_products_by_category, not just
    the LLM."""
    mock_discover, mock_inventory = _run_query("I need a hoodie for winter what would you suggest")
    mock_discover.assert_called_once()
    assert mock_discover.call_args.args[0] == "hoodie"
    # And must not ALSO fire the plain inventory trigger for the same message
    # (same collision class already fixed for the anchor-based recommendation
    # trigger).
    mock_inventory.assert_not_called()


def test_any_suggestions_phrasing_also_triggers_discovery():
    mock_discover, _ = _run_query("Any suggestions for a jacket?")
    mock_discover.assert_called_once()
    assert mock_discover.call_args.args[0] == "jacket"


def test_plain_greeting_does_not_trigger_discovery():
    mock_discover, mock_inventory = _run_query("Hi, how are you?")
    mock_discover.assert_not_called()
    mock_inventory.assert_not_called()
