"""
Live Product Intelligence: product questions must be answered from live
Shopify data, not stale RAG. Three gaps this closes:

1. find_products_by_title() didn't request handle/images/options, so
   get_inventory_status() could never build a real product link/image or
   label variant options (size/color) correctly - it always assumed
   variant.title was "the size", which breaks for color-only or
   multi-option products.
2. The trigger in customer_success_agent.py that decides when to call the
   live inventory tool depended on a hardcoded 7-word category list
   (hoodie/jacket/pants/...) - any merchant-specific product name, and every
   price question ("how much is...", "what's the price of..."), silently
   never reached Shopify at all and fell through to whatever RAG content
   happened to match instead.
3. Even once fetched, product_url/image_url/variant options were computed
   but never actually surfaced into the LLM's prompt context - the feature
   would have been functionally inert without wiring that through too.

Nothing here changes ambiguous/not-found/out-of-stock/unmanaged-inventory
handling - those are re-run unmodified from test_order_inventory_tools.py
to prove no regression, not duplicated here.
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
from src.agent.customer_success_agent import customer_success_agent, _enforce_no_ambiguous_product_claim  # noqa: E402


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── find_products_by_title: requests the new fields ──────────────────────────

def test_find_products_by_title_requests_handle_and_images():
    client = ShopifyClient("test-shop.myshopify.com", "shpat_test")
    captured = {}

    def fake_request(method, endpoint, data=None, params=None):
        captured.update(params or {})
        return {"data": {"products": []}}

    with patch.object(client, "_request", side_effect=fake_request):
        run(client.find_products_by_title("hoodie"))

    fields = captured.get("fields", "")
    assert "handle" in fields
    assert "images" in fields
    assert "options" in fields


# ── get_inventory_status: handle / image / product_url ───────────────────────

def _fake_product(**overrides):
    product = {
        "title": "Essential Hoodie",
        "handle": "essential-hoodie",
        "image": {"src": "https://cdn.shopify.com/essential-hoodie.jpg"},
        "options": [{"name": "Size"}],
        "variants": [{"title": "M", "sku": "EH-M", "price": "50.00", "inventory_quantity": 5,
                       "inventory_management": "shopify", "option1": "M"}],
    }
    product.update(overrides)
    return product


def test_inventory_status_includes_handle_image_and_product_url():
    tools = V3Tools()
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [_fake_product()]})):
        result = run(tools.get_inventory_status("essential hoodie", shop_domain="test-shop.myshopify.com", access_token="tok"))

    assert result["handle"] == "essential-hoodie"
    assert result["image_url"] == "https://cdn.shopify.com/essential-hoodie.jpg"
    assert result["product_url"] == "https://test-shop.myshopify.com/products/essential-hoodie"


def test_inventory_status_falls_back_to_images_array_when_no_featured_image():
    tools = V3Tools()
    product = _fake_product(image=None, images=[{"src": "https://cdn.shopify.com/from-array.jpg"}])
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [product]})):
        result = run(tools.get_inventory_status("essential hoodie", shop_domain="test-shop.myshopify.com", access_token="tok"))

    assert result["image_url"] == "https://cdn.shopify.com/from-array.jpg"


def test_inventory_status_missing_image_and_handle_handled_safely_not_fabricated():
    """No handle/image in Shopify's response - fields must be None, never guessed."""
    tools = V3Tools()
    product = _fake_product(handle=None, image=None, images=None)
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [product]})):
        result = run(tools.get_inventory_status("essential hoodie", shop_domain="test-shop.myshopify.com", access_token="tok"))

    assert result["success"] is True
    assert result["handle"] is None
    assert result["image_url"] is None
    assert result["product_url"] is None


# ── variant option decomposition (size/color, not "option1 = size") ─────────

def test_variant_options_decomposed_for_size_and_color_product():
    tools = V3Tools()
    product = {
        "title": "Classic Tee",
        "handle": "classic-tee",
        "options": [{"name": "Color"}, {"name": "Size"}],
        "variants": [
            {"title": "Red / M", "sku": "CT-R-M", "price": "20.00", "inventory_quantity": 3,
             "inventory_management": "shopify", "option1": "Red", "option2": "M"},
            {"title": "Blue / L", "sku": "CT-B-L", "price": "20.00", "inventory_quantity": 0,
             "inventory_management": "shopify", "option1": "Blue", "option2": "L"},
        ],
    }
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [product]})):
        result = run(tools.get_inventory_status("classic tee", shop_domain="test-shop.myshopify.com", access_token="tok"))

    variants = result["variants"]
    assert variants[0]["options"] == {"color": "Red", "size": "M"}
    assert variants[1]["options"] == {"color": "Blue", "size": "L"}
    # option1 is Color here, not Size — the old code would have mislabeled it.
    assert variants[0]["options"]["color"] == "Red"


def test_single_option_product_still_works_and_size_key_preserved():
    """No regression for the common single-option (size-only) case — both the
    legacy "size" key and the new "options" breakdown must agree."""
    tools = V3Tools()
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [_fake_product()]})):
        result = run(tools.get_inventory_status("essential hoodie", shop_domain="test-shop.myshopify.com", access_token="tok"))

    variant = result["variants"][0]
    assert variant["size"] == "M"
    assert variant["options"] == {"size": "M"}


def test_variant_with_no_options_metadata_omits_options_without_crashing():
    """Product has no 'options' array at all (older data shape) — must not
    crash, and must not invent an options breakdown it has no basis for."""
    tools = V3Tools()
    product = _fake_product(options=None)
    with patch.object(ShopifyClient, "find_products_by_title", new=AsyncMock(return_value={"products": [product]})):
        result = run(tools.get_inventory_status("essential hoodie", shop_domain="test-shop.myshopify.com", access_token="tok"))

    assert result["success"] is True
    assert result["variants"][0]["options"] == {}
    assert result["variants"][0]["size"] == "M"  # legacy field still populated


# ── trigger broadening: merchant-specific names and price questions ─────────

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
    """Runs process_customer_query with everything except the inventory tool
    mocked out, and returns the mock so callers can inspect call_args (or
    assert it was never called)."""
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(return_value=(_fake_ai_response("Here's what I found!"), "test_provider", "test_model", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}))), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))), \
         patch("src.agent.customer_success_agent.v3_tools.get_inventory_status", new=AsyncMock(return_value={"success": True, "product": "x", "variants": [], "message": "In stock."})) as mock_inv:
        run(customer_success_agent.process_customer_query(
            query=message,
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "email"},
            tenant_id="tenant-1",
            store_id=None,
            ticket_id="ticket-1",
        ))
    return mock_inv


def test_merchant_specific_product_name_reaches_live_lookup():
    """'Essential Crewneck' contains none of the old hardcoded category words
    (hoodie/jacket/pants/...) - the old trigger would have missed this."""
    mock_inv = _run_query("Do you have the Essential Crewneck in stock?")
    mock_inv.assert_called_once()
    assert mock_inv.call_args.args[0] == "essential crewneck"


def test_availability_query_with_trailing_qualifier_extracts_clean_product_name():
    """Regression for a bug found during live verification against the real
    HafsaClothing store: 'Is the Essential Hoodie V1 available in size M?'
    used to extract the entire trailing clause ('essential hoodie v1
    available in size m') instead of just the product name, because the
    pattern required 'available'/'in stock' to be the last thing in the
    message. Real customers routinely add a size/color qualifier after it."""
    mock_inv = _run_query("Is the Essential Hoodie V1 available in size M?")
    mock_inv.assert_called_once()
    assert mock_inv.call_args.args[0] == "essential hoodie v1"


def test_do_you_have_query_with_trailing_qualifier_extracts_clean_product_name():
    mock_inv = _run_query("Do you have the Essential Hoodie V1 available in size M?")
    mock_inv.assert_called_once()
    assert mock_inv.call_args.args[0] == "essential hoodie v1"


def test_price_question_how_much_is_reaches_live_lookup():
    mock_inv = _run_query("How much is the Winter Parka?")
    mock_inv.assert_called_once()
    assert mock_inv.call_args.args[0] == "winter parka"


def test_price_question_how_much_does_x_cost_reaches_live_lookup():
    mock_inv = _run_query("How much does the Winter Parka cost?")
    mock_inv.assert_called_once()
    assert mock_inv.call_args.args[0] == "winter parka"


def test_price_question_whats_the_price_of_reaches_live_lookup():
    mock_inv = _run_query("What's the price of the Denim Jacket?")
    mock_inv.assert_called_once()
    assert mock_inv.call_args.args[0] == "denim jacket"


def test_unrelated_message_does_not_trigger_a_product_search():
    """The keyword gate must still exist — an ordinary message with none of
    the trigger phrases must never reach Shopify."""
    mock_inv = _run_query("Thanks so much for your help today!")
    mock_inv.assert_not_called()


def test_legacy_category_word_still_works_unchanged():
    """The old hardcoded-word path is kept as a fallback, not removed."""
    mock_inv = _run_query("Is the hoodie available?")
    mock_inv.assert_called_once()


# ── tenant/brand isolation: credentials are per-call, never shared ──────────

def test_inventory_lookup_uses_the_specific_brands_credentials_only():
    calls = []

    async def fake_find(self, query, limit=250):
        calls.append(self.shop_domain)
        return {"success": True, "products": [], "count": 0}

    with patch.object(ShopifyClient, "find_products_by_title", fake_find):
        tools = V3Tools()
        run(tools.get_inventory_status("hoodie", shop_domain="brand-a.myshopify.com", access_token="tok-a"))
        run(tools.get_inventory_status("hoodie", shop_domain="brand-b.myshopify.com", access_token="tok-b"))

    assert calls == ["brand-a.myshopify.com", "brand-b.myshopify.com"]


# ── find_products_by_title: word-boundary matching (V1/V10 substring fix) ───

def test_find_products_by_title_precise_name_does_not_match_numbered_siblings():
    """Confirmed live against the real HafsaClothing store: a bare substring
    check treated 'essential hoodie v1' as present inside 'essential hoodie
    v10' (v1 is a literal prefix of v10), producing a false "ambiguous"
    result for a precisely-named product."""
    client = ShopifyClient("test-shop.myshopify.com", "shpat_test")
    products = [
        {"id": 1, "title": "Essential Hoodie V1", "status": "active", "variants": []},
        {"id": 2, "title": "Essential Hoodie V10", "status": "active", "variants": []},
        {"id": 3, "title": "Essential Hoodie V11", "status": "active", "variants": []},
    ]
    with patch.object(client, "_request", return_value={"data": {"products": products}}):
        result = run(client.find_products_by_title("essential hoodie v1"))
    assert result["count"] == 1
    assert result["products"][0]["title"] == "Essential Hoodie V1"


def test_find_products_by_title_partial_word_search_still_matches_every_variant():
    """Word-boundary matching must not regress ordinary partial/prefix
    searches — "hoodie" alone must still find every hoodie."""
    client = ShopifyClient("test-shop.myshopify.com", "shpat_test")
    products = [
        {"id": 1, "title": "Essential Hoodie V1", "status": "active", "variants": []},
        {"id": 2, "title": "Essential Hoodie V10", "status": "active", "variants": []},
        {"id": 3, "title": "Zip-Up Hoodie", "status": "active", "variants": []},
    ]
    with patch.object(client, "_request", return_value={"data": {"products": products}}):
        result = run(client.find_products_by_title("hoodie"))
    assert result["count"] == 3


# ── deterministic guard: ambiguous product lookups can never yield a ────────
# ── confirmed product/availability/price claim, even if the model tries ────

def test_guard_overrides_a_confident_claim_when_lookup_is_ambiguous():
    structured = {"reply_body": "Yes, the Essential Hoodie V1 is in stock in size M!"}
    inv = {"success": True, "ambiguous": True, "matches": ["Essential Hoodie V1", "Essential Hoodie V10"]}
    result = _enforce_no_ambiguous_product_claim(structured, inv)
    assert "in stock" not in result["reply_body"].lower()
    assert "essential hoodie v1" in result["reply_body"].lower()
    assert "essential hoodie v10" in result["reply_body"].lower()


def test_guard_overrides_a_confident_price_claim_when_lookup_is_ambiguous():
    structured = {"reply_body": "The Essential Hoodie V1 is priced at $120.00."}
    inv = {"success": True, "ambiguous": True, "matches": ["Essential Hoodie V1", "Essential Hoodie V10"]}
    result = _enforce_no_ambiguous_product_claim(structured, inv)
    assert "$120" not in result["reply_body"]
    assert "which one" in result["reply_body"].lower() or "let me know" in result["reply_body"].lower()


def test_guard_handles_ambiguous_with_no_matches_list_safely():
    structured = {"reply_body": "It's in stock."}
    inv = {"success": True, "ambiguous": True, "matches": []}
    result = _enforce_no_ambiguous_product_claim(structured, inv)
    assert "in stock" not in result["reply_body"].lower()


def test_guard_leaves_single_exact_match_reply_untouched():
    structured = {"reply_body": "Yes, the Essential Hoodie V10 is in stock in every size."}
    inv = {"success": True, "product": "Essential Hoodie V10", "variants": []}
    result = _enforce_no_ambiguous_product_claim(structured, inv)
    assert result["reply_body"] == "Yes, the Essential Hoodie V10 is in stock in every size."


def test_guard_leaves_not_found_reply_untouched():
    structured = {"reply_body": "I couldn't find that product in our collection."}
    inv = {"success": False, "message": "I couldn't find 'x' in our current collection."}
    result = _enforce_no_ambiguous_product_claim(structured, inv)
    assert result["reply_body"] == "I couldn't find that product in our collection."


def test_guard_leaves_reply_untouched_when_no_inventory_lookup_ran():
    structured = {"reply_body": "Sure, happy to help with something else!"}
    result = _enforce_no_ambiguous_product_claim(structured, None)
    assert result["reply_body"] == "Sure, happy to help with something else!"


# ── same guard, exercised through the full agent pipeline ───────────────────

def _run_query_full(message: str, inventory_result: dict, ai_reply_body: str):
    """Like _run_query, but lets the caller control exactly what the live
    tool returned and what the model said, and returns the full result dict
    so the final reply_body can be inspected."""
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(return_value=(_fake_ai_response(ai_reply_body), "test_provider", "test_model", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}))), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))), \
         patch("src.agent.customer_success_agent.v3_tools.get_inventory_status", new=AsyncMock(return_value=inventory_result)):
        result = run(customer_success_agent.process_customer_query(
            query=message,
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "email"},
            tenant_id="tenant-1",
            store_id=None,
            ticket_id="ticket-1",
        ))
    return result


def test_ambiguous_availability_question_asks_for_clarification_end_to_end():
    """Reproduces the exact live finding: the model answers with a specific
    claim despite an ambiguous tool result — the final reply the customer
    actually receives must be the clarification, not the model's claim."""
    ambiguous = {
        "success": True, "ambiguous": True,
        "matches": ["Essential Hoodie V1", "Essential Hoodie V10", "Essential Hoodie V11"],
        "message": "I found a few products matching 'essential hoodie v1': Essential Hoodie V1, Essential Hoodie V10, Essential Hoodie V11. Which one did you mean?",
    }
    result = _run_query_full(
        "Is the Essential Hoodie V1 available in size M?",
        ambiguous,
        "Yes, the Essential Hoodie V1 is available in size medium!",
    )
    assert "available in size medium" not in result["reply_body"].lower()
    assert "essential hoodie v1" in result["reply_body"].lower()
    assert "essential hoodie v10" in result["reply_body"].lower()


def test_ambiguous_price_question_asks_for_clarification_end_to_end():
    ambiguous = {
        "success": True, "ambiguous": True,
        "matches": ["Essential Hoodie V1", "Essential Hoodie V10", "Essential Hoodie V11"],
        "message": "I found a few products matching 'essential hoodie v1': Essential Hoodie V1, Essential Hoodie V10, Essential Hoodie V11. Which one did you mean?",
    }
    result = _run_query_full(
        "How much is the Essential Hoodie V1?",
        ambiguous,
        "The Essential Hoodie V1 is priced at $120.00.",
    )
    assert "$120" not in result["reply_body"]
    assert "essential hoodie v1" in result["reply_body"].lower()


def test_single_exact_match_still_answers_normally_end_to_end():
    single = {
        "success": True, "product": "Essential Hoodie V10",
        "handle": "essential-hoodie-v10", "image_url": None,
        "product_url": "https://test-shop.myshopify.com/products/essential-hoodie-v10",
        "variants": [{"size": "M", "options": {"size": "M"}, "sku": "HD-10-M", "price": "120.00", "in_stock": True, "quantity": 5}],
        "message": "Yes, Essential Hoodie V10 is in stock.",
    }
    result = _run_query_full(
        "Is the Essential Hoodie V10 in stock?",
        single,
        "Yes, the Essential Hoodie V10 is in stock and available in size M!",
    )
    # Greeting/signature wrapping (unrelated, pre-existing behavior) still
    # applies on top — the guard must leave the model's own content untouched.
    assert "Yes, the Essential Hoodie V10 is in stock and available in size M!" in result["reply_body"]


def test_not_found_still_behaves_normally_end_to_end():
    not_found = {"success": False, "message": "I couldn't find 'galactic space boots' in our current collection. Want me to check something else for you?"}
    result = _run_query_full(
        "Do you have the Galactic Space Boots in stock?",
        not_found,
        "I'm sorry, we don't carry Galactic Space Boots.",
    )
    assert "I'm sorry, we don't carry Galactic Space Boots." in result["reply_body"]
