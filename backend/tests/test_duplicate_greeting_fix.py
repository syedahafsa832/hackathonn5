"""
Duplicate greeting bug: "Hey [customer@email.com],\\n\\nHey!\\n\\nwith care,\\nLuna".

Root cause: two greeting layers existed in customer_success_agent.py's
email post-processing (process_customer_query) —
1. customer_info["name"] was sometimes a raw email address (e.g. the Gmail
   poller falls back to the sender's address when there's no display
   name), and _known_customer_name() only rejected a fixed set of
   placeholder words ("there", "customer", ...) — never email-shaped
   strings — so it passed straight through as if it were a real name.
2. The safety-net greeting injection then checked whether that `name`
   string (possibly the email) appeared in the first 30 characters of the
   model's own reply. The model, uninfluenced by the bad "name", correctly
   wrote its own generic greeting ("Hey!") — which never contains the
   email — so the dedup check failed and a second, email-addressed
   greeting was prepended on top.

Fix: _known_customer_name() now also rejects email-shaped strings, and the
dedup check (_reply_already_has_greeting) now recognizes ANY greeting-
shaped opening in the model's reply, not just one containing a specific
name string — making it correct regardless of what `name` resolves to.
"""
import os
import sys
import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.agent.customer_success_agent import (  # noqa: E402
    customer_success_agent, _known_customer_name, _reply_already_has_greeting,
)
from src.services.intent_detector import IntentResult  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _fake_ai_response(reply_body: str, intent: str = "general_inquiry"):
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


def _run_email(query: str, reply_body: str, customer_name=None):
    """Runs process_customer_query for real on the EMAIL path (channel !=
    'chat', so the post-processing greeting safety-net actually executes),
    with only network-touching edges mocked."""
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion",
               new=AsyncMock(return_value=(_fake_ai_response(reply_body), "test_provider", "test_model", _FAKE_USAGE))), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.v3_tools.get_inventory_status", new=AsyncMock(return_value={"success": False, "message": "not found"})), \
         patch("src.agent.customer_success_agent.v3_tools.get_product_recommendations", new=AsyncMock(return_value={"success": False, "message": "none"})), \
         patch("src.agent.customer_success_agent.v3_tools.discover_products_by_category", new=AsyncMock(return_value={"success": False, "message": "none"})), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=NO_ACTION)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.lib.supabase_client.supabase_select", return_value=[]):
        result = run(customer_success_agent.process_customer_query(
            query=query,
            customer_info={"name": customer_name, "email": "muhammad.specials@gmail.com", "channel": "email"},
            tenant_id="tenant-1", store_id="brand-1", ticket_id="ticket-1",
        ))
    return result


# ══════════════════════════════════════════════════════════════════════════
# Unit tests
# ══════════════════════════════════════════════════════════════════════════

# 6. Customer email is never used as a customer name.
def test_known_customer_name_rejects_email_shaped_strings():
    assert _known_customer_name("muhammad.specials@gmail.com") is None
    assert _known_customer_name("customer@example.com") is None


def test_known_customer_name_still_accepts_real_names():
    assert _known_customer_name("Bushra") == "Bushra"
    assert _known_customer_name("Muhammad Ali") == "Muhammad Ali"


def _greeting_cases():
    return [
        ("Hey! How can I help?", True),
        ("Hi! How can I help?", True),
        ("Hello there, happy to help.", True),
        ("Dear Bushra, thank you for reaching out.", True),
        ("Good morning! Here's what I found.", True),
        ("Thanks for reaching out! Let me check.", True),
        ("Thank you for your patience.", True),
        ("Greetings! How can we help?", True),
        ("Your order shipped yesterday.", False),
        ("It looks like your order is on its way.", False),
        ("", False),
    ]


def test_reply_already_has_greeting_recognizes_common_openers():
    for reply, expected in _greeting_cases():
        assert _reply_already_has_greeting(reply) is expected, f"{reply!r} expected {expected}"


# ══════════════════════════════════════════════════════════════════════════
# Integration — the actual reported bug, end-to-end through process_customer_query
# ══════════════════════════════════════════════════════════════════════════

# 7. A simple "hi" produces at most one greeting; email-as-name never appears.
def test_email_shaped_name_with_ai_greeting_produces_a_single_greeting():
    result = _run_email("hi", reply_body="Hey!\n\nHow can I help you today?", customer_name="muhammad.specials@gmail.com")
    reply = result["reply_body"]
    assert "muhammad.specials@gmail.com" not in reply
    assert reply.count("Hey") == 1
    assert reply.startswith("Hey!")


def test_hello_greeting_is_not_duplicated():
    result = _run_email("hello", reply_body="Hi! How can I help?", customer_name=None)
    reply = result["reply_body"]
    assert reply.startswith("Hi!")
    assert "Hey there," not in reply


# 8. AI-generated greetings are not duplicated by post-processing, for a
# realistic order-question reply too (one greeting at most).
def test_order_question_reply_with_ai_greeting_is_not_duplicated():
    result = _run_email(
        "Where is my order?",
        reply_body="Hey! Thanks for reaching out — let me check on that for you.",
        customer_name="muhammad.specials@gmail.com",
    )
    reply = result["reply_body"]
    assert "muhammad.specials@gmail.com" not in reply
    assert reply.count("Hey!") == 1


# 9. A response with genuinely no AI greeting still receives the system's
# safety-net greeting (using a real name when known, otherwise the neutral
# "there" idiom — never the email).
def test_reply_with_no_ai_greeting_still_gets_the_safety_net_greeting():
    result = _run_email("track my order", reply_body="Your order shipped yesterday.", customer_name="Bushra")
    reply = result["reply_body"]
    assert reply.startswith("Hey Bushra,")
    assert "Your order shipped yesterday." in reply


def test_reply_with_no_ai_greeting_and_no_known_name_uses_neutral_fallback_not_email():
    result = _run_email("track my order", reply_body="Your order shipped yesterday.", customer_name="muhammad.specials@gmail.com")
    reply = result["reply_body"]
    assert reply.startswith("Hey there,")
    assert "muhammad.specials@gmail.com" not in reply
