"""
Focused regression tests for ticket #ec3daa7f's 3 confirmed fixes:

1. Product-search pronoun bug: "...black maxi dress... do you have it in
   stock?" searched inventory for "it" instead of the actual product named
   earlier in the same message.
2. Product link reaching the response-generation prompt when the inventory
   lookup succeeds with a product_url (already-existing tool_context code -
   this proves it, not a new feature).
3. Double greeting: "Heyyy AI CODERS!" (a real greeting) wasn't recognized
   by _reply_already_has_greeting because of the exact-word "hey" regex,
   so a duplicate "Hey AI," got prepended on top - also, the prefix used
   .split()[0], turning "AI CODERS" into just "AI".
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
    customer_success_agent,
    _reply_already_has_greeting,
)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _fake_ai_response(reply_body: str):
    msg = MagicMock()
    msg.content = json.dumps({"intent": "product_inquiry", "reply_body": reply_body, "risk_level": "low"})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _run_query(message: str, inv_return=None):
    inv_return = inv_return or {"success": False, "message": "not found"}
    captured_prompt = {}

    async def fake_completion(messages, **kwargs):
        captured_prompt["system"] = messages[0]["content"]
        return (_fake_ai_response("Here's what I found!"), "test_provider", "test_model",
                {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1})

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=fake_completion)), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))), \
         patch("src.agent.customer_success_agent.v3_tools.get_inventory_status", new=AsyncMock(return_value=inv_return)) as mock_inv:
        result = run(customer_success_agent.process_customer_query(
            query=message,
            customer_info={"name": "AI CODERS", "email": "aicoders123@gmail.com", "channel": "email"},
            tenant_id="tenant-1",
            store_id=None,
            ticket_id="ticket-1",
        ))
    return result, mock_inv, captured_prompt


# ── 1. Pronoun bug ───────────────────────────────────────────────────────

def test_pronoun_it_resolves_to_the_product_named_earlier_in_the_message():
    message = "hey yo luna, wanna know more about the black maxi dress, do you have it in stock?"
    result, mock_inv, _ = _run_query(message)
    mock_inv.assert_called_once()
    searched_term = mock_inv.call_args.args[0]
    assert searched_term != "it"
    assert "dress" in searched_term


def test_explicit_product_name_still_works_unchanged():
    """Normal explicit-name phrasing (no pronoun involved) is unaffected."""
    message = "do you have the Essential Hoodie in stock?"
    result, mock_inv, _ = _run_query(message)
    mock_inv.assert_called_once()
    assert mock_inv.call_args.args[0] == "essential hoodie"


# ── 2. Product link reaches the response-generation prompt ──────────────

def test_product_url_reaches_the_prompt_on_success():
    inv_success = {
        "success": True, "message": "Black Wrap Maxi Dress is in stock!",
        "product_url": "https://store.myshopify.com/products/black-wrap-maxi-dress",
        "image_url": "https://cdn.shopify.com/dress.jpg",
    }
    message = "do you have the black wrap maxi dress in stock? and can you send an image?"
    result, mock_inv, captured_prompt = _run_query(message, inv_return=inv_success)
    assert "https://store.myshopify.com/products/black-wrap-maxi-dress" in captured_prompt["system"]
    assert "can't attach product images" in captured_prompt["system"].lower()


# ── 3. Double greeting ────────────────────────────────────────────────────

def test_elongated_greeting_variants_are_recognized():
    assert _reply_already_has_greeting("Heyyy AI CODERS! I'm so excited...") is True
    assert _reply_already_has_greeting("Hiiii there!") is True
    assert _reply_already_has_greeting("Hellooo!") is True


def test_normal_message_without_greeting_still_gets_the_deterministic_prefix():
    assert _reply_already_has_greeting("Your order shipped yesterday.") is False


def test_full_pipeline_does_not_double_greet_two_word_name():
    """End-to-end: the model's own "Heyyy AI CODERS!" opener must survive
    unprefixed, and if the deterministic prefix ever does fire for a
    two-word name, it must never truncate to just the first word."""
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion",
               new=AsyncMock(return_value=(_fake_ai_response("Heyyy AI CODERS! Great to hear from you!"), "p", "m",
                                            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 1, "attempts": 1}))), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))):
        result = run(customer_success_agent.process_customer_query(
            query="hey yo luna, whatsuppppp",
            customer_info={"name": "AI CODERS", "email": "aicoders123@gmail.com", "channel": "email"},
            tenant_id="tenant-1", store_id=None, ticket_id="ticket-1",
        ))
    assert result["reply_body"].count("Heyyy") == 1
    assert "Hey AI," not in result["reply_body"]
