"""
Product Recommendations MVP — deterministic, live-Shopify-grounded, no ML,
no per-merchant model, reusing the same ShopifyClient/tools/agent
architecture the Live Product Intelligence work already established.

Scoring is deterministic (shared product_type, tag overlap, vendor) against
data already fetched from Shopify — nothing invented. Complementary-intent
questions ("what goes with this") are handled honestly: no real
bought-together/pairing data exists anywhere in this architecture (no
Storefront API, no merchant-configured pairing table), so that intent never
silently substitutes same-type results and calls them complementary.

Every failure path (not-found anchor, ambiguous anchor, no candidates, no
pairing data, Shopify failure) is guarded by
_enforce_no_ungrounded_recommendation - mirrors the precedent set by
_enforce_no_ambiguous_product_claim: the LLM's own text is never trusted for
whether a recommendation is grounded, the tool's own result always wins.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.tools import V3Tools  # noqa: E402
from src.services.shopify_service import ShopifyClient  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent, _enforce_no_ungrounded_recommendation  # noqa: E402


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _product(id_, title, product_type="Hoodie", vendor="Acme", tags="fabric:Cotton,fit:oversized", price="50.00", handle=None, quantity=5, inv_mgmt="shopify"):
    return {
        "id": id_, "title": title, "status": "active",
        "product_type": product_type, "vendor": vendor, "tags": tags,
        "handle": handle or title.lower().replace(" ", "-"),
        "image": {"src": f"https://cdn.example.com/{id_}.jpg"},
        "variants": [{"title": "M", "price": price, "inventory_quantity": quantity, "inventory_management": inv_mgmt}],
    }


# ── get_product_recommendations: deterministic scoring ──────────────────────

def test_recommendations_score_same_type_and_shared_tags_highest():
    tools = V3Tools()
    anchor = _product(1, "Essential Hoodie", product_type="Hoodie", tags="fabric:Cotton,fit:oversized")
    same_type_more_tags = _product(2, "Premium Hoodie", product_type="Hoodie", tags="fabric:Cotton,fit:oversized,color:black")
    same_type_no_tags = _product(3, "Zip Hoodie", product_type="Hoodie", tags="")
    different_type = _product(4, "Denim Jeans", product_type="Jeans", tags="fabric:Cotton")

    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [anchor], "count": 1})), \
         patch.object(ShopifyClient, "list_active_products", new=AsyncMock(return_value=[anchor, same_type_more_tags, same_type_no_tags, different_type])):
        result = run(tools.get_product_recommendations("Essential Hoodie", shop_domain="test-shop.myshopify.com", access_token="tok"))

    assert result["success"] is True
    titles = [r["title"] for r in result["recommendations"]]
    assert "Essential Hoodie" not in titles  # anchor excluded from its own recommendations
    assert titles[0] == "Premium Hoodie"  # highest score: same type + most shared tags
    assert "Denim Jeans" in titles  # weak (different type, one shared tag via fabric:Cotton) still included with a low score
    top = result["recommendations"][0]
    assert "same product type" in top["matching_reason"]
    assert "shares tags" in top["matching_reason"]


def test_recommendations_exclude_the_anchor_product_itself():
    tools = V3Tools()
    anchor = _product(1, "Essential Hoodie")
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [anchor], "count": 1})), \
         patch.object(ShopifyClient, "list_active_products", new=AsyncMock(return_value=[anchor])):
        result = run(tools.get_product_recommendations("Essential Hoodie", shop_domain="test-shop.myshopify.com", access_token="tok"))
    assert result.get("no_candidates") is True


def test_recommendations_report_unavailable_candidate_honestly():
    tools = V3Tools()
    anchor = _product(1, "Essential Hoodie")
    out_of_stock = _product(2, "Premium Hoodie", quantity=0)
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [anchor], "count": 1})), \
         patch.object(ShopifyClient, "list_active_products", new=AsyncMock(return_value=[anchor, out_of_stock])):
        result = run(tools.get_product_recommendations("Essential Hoodie", shop_domain="test-shop.myshopify.com", access_token="tok"))
    assert result["recommendations"][0]["available"] is False


def test_recommendations_untracked_inventory_not_reported_unavailable():
    tools = V3Tools()
    anchor = _product(1, "Essential Hoodie")
    untracked = _product(2, "Premium Hoodie", quantity=0, inv_mgmt=None)
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [anchor], "count": 1})), \
         patch.object(ShopifyClient, "list_active_products", new=AsyncMock(return_value=[anchor, untracked])):
        result = run(tools.get_product_recommendations("Essential Hoodie", shop_domain="test-shop.myshopify.com", access_token="tok"))
    assert result["recommendations"][0]["available"] is True


def test_recommendations_missing_image_and_handle_never_fabricated():
    tools = V3Tools()
    anchor = _product(1, "Essential Hoodie")
    no_media = _product(2, "Premium Hoodie")
    no_media["image"] = None
    no_media["images"] = None
    no_media["handle"] = None
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [anchor], "count": 1})), \
         patch.object(ShopifyClient, "list_active_products", new=AsyncMock(return_value=[anchor, no_media])):
        result = run(tools.get_product_recommendations("Essential Hoodie", shop_domain="test-shop.myshopify.com", access_token="tok"))
    rec = result["recommendations"][0]
    assert rec["image_url"] is None
    assert rec["handle"] is None
    assert rec["product_url"] is None


def test_recommendations_product_url_built_from_shop_domain_and_handle():
    tools = V3Tools()
    anchor = _product(1, "Essential Hoodie")
    candidate = _product(2, "Premium Hoodie", handle="premium-hoodie")
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [anchor], "count": 1})), \
         patch.object(ShopifyClient, "list_active_products", new=AsyncMock(return_value=[anchor, candidate])):
        result = run(tools.get_product_recommendations("Essential Hoodie", shop_domain="test-shop.myshopify.com", access_token="tok"))
    assert result["recommendations"][0]["product_url"] == "https://test-shop.myshopify.com/products/premium-hoodie"


def test_recommendations_anchor_not_found_is_honest():
    tools = V3Tools()
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [], "count": 0})):
        result = run(tools.get_product_recommendations("Galactic Space Boots", shop_domain="test-shop.myshopify.com", access_token="tok"))
    assert result["success"] is False
    assert "couldn't find" in result["message"].lower()


def test_recommendations_ambiguous_anchor_asks_for_clarification():
    tools = V3Tools()
    matches = [_product(1, "Essential Hoodie V1"), _product(2, "Essential Hoodie V10")]
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": matches, "count": 2})):
        result = run(tools.get_product_recommendations("Essential Hoodie", shop_domain="test-shop.myshopify.com", access_token="tok"))
    assert result.get("ambiguous") is True
    assert "Essential Hoodie V1" in result["matches"]


def test_recommendations_complementary_intent_never_fakes_pairing_data():
    """The critical distinction: complementary must NOT silently become
    "same type" results relabeled as complementary."""
    tools = V3Tools()
    result = run(tools.get_product_recommendations(
        "Essential Hoodie", shop_domain="test-shop.myshopify.com", access_token="tok", intent="complementary",
    ))
    assert result["success"] is False
    assert result.get("no_pairing_data") is True
    assert "don't have reliable data" in result["message"].lower() or "don't want to guess" in result["message"].lower()


def test_recommendations_shopify_failure_is_honest_not_invented():
    tools = V3Tools()
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(side_effect=Exception("connection refused"))):
        result = run(tools.get_product_recommendations("Essential Hoodie", shop_domain="test-shop.myshopify.com", access_token="tok"))
    assert result["success"] is False


def test_recommendations_without_credentials_escalates_not_guesses():
    tools = V3Tools()
    result = run(tools.get_product_recommendations("Essential Hoodie"))
    assert result["success"] is False


# ── tenant/brand isolation: credentials are per-call, never shared ──────────

def test_recommendations_use_the_specific_brands_credentials_only():
    calls = []

    async def fake_find(self, query, limit=250):
        calls.append(self.shop_domain)
        return {"success": True, "products": [_product(1, "Essential Hoodie")], "count": 1}

    async def fake_list(self, limit=250):
        return [_product(1, "Essential Hoodie")]

    with patch.object(ShopifyClient, "find_products_by_title", fake_find), \
         patch.object(ShopifyClient, "list_active_products", fake_list):
        tools = V3Tools()
        run(tools.get_product_recommendations("hoodie", shop_domain="brand-a.myshopify.com", access_token="tok-a"))
        run(tools.get_product_recommendations("hoodie", shop_domain="brand-b.myshopify.com", access_token="tok-b"))

    assert calls == ["brand-a.myshopify.com", "brand-b.myshopify.com"]


# ── deterministic guard: ungrounded recommendations can never reach the ─────
# ── customer, even if the model tries ────────────────────────────────────────

def test_guard_leaves_genuine_success_untouched():
    structured = {"reply_body": "You might also like the Premium Hoodie, since it shares the same style!"}
    rec = {"success": True, "recommendations": [{"title": "Premium Hoodie"}], "message": "Based on Essential Hoodie, you might also like: Premium Hoodie."}
    result = _enforce_no_ungrounded_recommendation(structured, rec)
    assert result["reply_body"] == "You might also like the Premium Hoodie, since it shares the same style!"


def test_guard_overrides_when_no_candidates_but_model_invents_one():
    structured = {"reply_body": "You'd love our Deluxe Joggers with that!"}
    rec = {"success": True, "no_candidates": True, "message": "I don't have enough similar products to confidently recommend something alongside Essential Hoodie right now."}
    result = _enforce_no_ungrounded_recommendation(structured, rec)
    assert "Deluxe Joggers" not in result["reply_body"]
    assert result["reply_body"] == rec["message"]


def test_guard_overrides_when_ambiguous_but_model_answers_anyway():
    structured = {"reply_body": "Sure! You'd like the matching joggers."}
    rec = {"success": True, "ambiguous": True, "matches": ["Essential Hoodie V1", "Essential Hoodie V10"], "message": "I found a few products matching 'essential hoodie'. Which one did you mean?"}
    result = _enforce_no_ungrounded_recommendation(structured, rec)
    assert result["reply_body"] == rec["message"]


def test_guard_overrides_complementary_when_model_invents_a_pairing():
    structured = {"reply_body": "These joggers would go perfectly with that hoodie!"}
    rec = {"success": False, "no_pairing_data": True, "message": "I don't have reliable data on what's specifically meant to be paired with that item, so I don't want to guess."}
    result = _enforce_no_ungrounded_recommendation(structured, rec)
    assert "joggers would go perfectly" not in result["reply_body"]
    assert result["reply_body"] == rec["message"]


def test_guard_leaves_reply_untouched_when_no_recommendation_lookup_ran():
    structured = {"reply_body": "Sure, happy to help with something else!"}
    result = _enforce_no_ungrounded_recommendation(structured, None)
    assert result["reply_body"] == "Sure, happy to help with something else!"


# ── trigger broadening: MVP intents reach the live tool ──────────────────────

def _fake_ai_response(reply_body: str):
    import json
    msg = MagicMock()
    msg.content = json.dumps({"intent": "product_inquiry", "reply_body": reply_body, "risk_level": "low"})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _run_query(message: str):
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(return_value=(_fake_ai_response("Here's what I found!"), "test_provider", "test_model", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}))), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))), \
         patch("src.agent.customer_success_agent.v3_tools.get_product_recommendations", new=AsyncMock(return_value={"success": True, "no_candidates": True, "message": "no candidates"})) as mock_rec:
        run(customer_success_agent.process_customer_query(
            query=message,
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "email"},
            tenant_id="tenant-1",
            store_id=None,
            ticket_id="ticket-1",
        ))
    return mock_rec


def test_similar_intent_show_me_something_similar():
    mock_rec = _run_query("Show me something similar to the Essential Hoodie.")
    mock_rec.assert_called_once()
    assert mock_rec.call_args.args[0] == "essential hoodie"
    assert mock_rec.call_args.kwargs["intent"] == "similar"


def test_similar_intent_anything_like_this():
    mock_rec = _run_query("Do you have anything like the Essential Hoodie?")
    mock_rec.assert_called_once()
    assert mock_rec.call_args.kwargs["intent"] == "similar"


def test_similar_intent_alternatives_to():
    mock_rec = _run_query("Any alternatives to the Essential Hoodie?")
    mock_rec.assert_called_once()
    assert mock_rec.call_args.args[0] == "essential hoodie"


def test_complementary_intent_goes_well_with():
    mock_rec = _run_query("What goes well with the Essential Hoodie?")
    mock_rec.assert_called_once()
    assert mock_rec.call_args.kwargs["intent"] == "complementary"
    assert mock_rec.call_args.args[0] == "essential hoodie"


def test_complementary_intent_what_can_i_wear_with():
    mock_rec = _run_query("What can I wear with the Essential Hoodie?")
    mock_rec.assert_called_once()
    assert mock_rec.call_args.kwargs["intent"] == "complementary"


def test_pronoun_only_anchor_does_not_trigger_a_search_for_the_literal_word_this():
    """No cross-turn anchor tracking exists yet - "this" alone must not
    become a Shopify search for the literal word "this"."""
    mock_rec = _run_query("Show me something similar to this.")
    mock_rec.assert_not_called()


def test_unrelated_message_does_not_trigger_recommendation_lookup():
    mock_rec = _run_query("Thanks so much for your help today!")
    mock_rec.assert_not_called()


def test_recommendation_shaped_message_does_not_also_fire_the_plain_inventory_trigger():
    """Regression for a bug found during live verification against the real
    HafsaClothing store: "Do you have anything like the Galactic Space
    Boots?" matches BOTH the inventory keyword gate ("do you have") and the
    recommendation keyword gate ("anything like"). Before the fix, both
    triggers fired independently, producing a second, wasted, lower-quality
    Shopify lookup for the literal string "anything like the galactic space
    boots" instead of just the product name. Only the recommendation lookup
    should run."""
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(return_value=(_fake_ai_response("Here's what I found!"), "test_provider", "test_model", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}))), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))), \
         patch("src.agent.customer_success_agent.v3_tools.get_product_recommendations", new=AsyncMock(return_value={"success": False, "message": "not found"})) as mock_rec, \
         patch("src.agent.customer_success_agent.v3_tools.get_inventory_status", new=AsyncMock(return_value={"success": False, "message": "not found"})) as mock_inv:
        run(customer_success_agent.process_customer_query(
            query="Do you have anything like the Galactic Space Boots?",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "email"},
            tenant_id="tenant-1",
            store_id=None,
            ticket_id="ticket-1",
        ))

    mock_rec.assert_called_once()
    assert mock_rec.call_args.args[0] == "galactic space boots"
    mock_inv.assert_not_called()
