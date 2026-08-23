"""
Product recommendation conversational gaps — confirmed by focused
verification (specs/007-autopilot-automation/pre-autopilot-safety-fixes.md):

1. Pronoun/context follow-ups ("show me that one", "what about this one?")
   previously fell through to a fully ungrounded LLM reply — no tool call
   at all, so none of the existing anti-hallucination guards could fire.
2. Color/variant follow-ups ("do you have it in black?") were captured by
   the plain inventory-lookup gate as if the whole phrase ("this in another
   color") were a literal product name — an honest but useless "couldn't
   find that" instead of ever looking up the real product.

Fixed by _resolve_recent_product_anchor(): when the current message alone
doesn't name a product, look backward through this conversation's own chat
history (already embedded in `query` by v2_chat_widget.py) for the most
recently mentioned real product, using the same extraction patterns already
trusted for the current message. Still never a guess — whatever's found
still has to resolve through the same live Shopify title search before
reaching a reply. When history has nothing resolvable either, a deterministic
clarifying question is forced via the existing guard machinery
(_enforce_no_ambiguous_product_claim / _enforce_no_ungrounded_recommendation)
— never silence, never a hallucinated answer.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402
from src.agent.customer_success_agent import (  # noqa: E402
    customer_success_agent,
    _resolve_recent_product_anchor,
    _enforce_no_ambiguous_product_claim,
)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── _resolve_recent_product_anchor: pure unit tests, no agent involved ──────

def test_no_history_marker_returns_none():
    assert _resolve_recent_product_anchor("Do you have this in another color?") is None


def test_history_with_no_product_mention_returns_none():
    query = (
        "[CHAT HISTORY — earlier in this conversation:]\n"
        "Customer: Hi, when will my order arrive?\n"
        "Luna: Let me check that for you — could you share your order number?\n"
        "[END CHAT HISTORY]\n\n"
        "Customer: What about a smaller size?"
    )
    assert _resolve_recent_product_anchor(query) is None


def test_history_resolves_a_product_luna_previously_named():
    query = (
        "[CHAT HISTORY — earlier in this conversation:]\n"
        "Customer: Do you have the Essential Hoodie in stock?\n"
        "Luna: Yes, the Essential Hoodie is in stock and ships in 2-3 days.\n"
        "[END CHAT HISTORY]\n\n"
        "Customer: Do you have it in another color?"
    )
    assert _resolve_recent_product_anchor(query) == "essential hoodie"


def test_history_resolution_prefers_the_most_recent_mention():
    """Two different products discussed earlier — the customer's follow-up
    is about whatever was discussed LAST, not the first thing mentioned."""
    query = (
        "[CHAT HISTORY — earlier in this conversation:]\n"
        "Customer: Do you have the Essential Hoodie in stock?\n"
        "Luna: Yes, the Essential Hoodie is in stock.\n"
        "Customer: What about the Winter Parka?\n"
        "Luna: The Winter Parka is also in stock.\n"
        "[END CHAT HISTORY]\n\n"
        "Customer: Do you have it in black?"
    )
    assert "winter parka" in _resolve_recent_product_anchor(query)


def test_history_pronoun_only_lines_are_skipped_not_returned():
    """A history line that is itself just a pronoun reference must not be
    returned as if it were a real product name."""
    query = (
        "[CHAT HISTORY — earlier in this conversation:]\n"
        "Customer: Do you have anything similar to this?\n"
        "Luna: Could you tell me which product you mean?\n"
        "[END CHAT HISTORY]\n\n"
        "Customer: What about that one?"
    )
    assert _resolve_recent_product_anchor(query) is None


# ── _enforce_no_ambiguous_product_claim: needs_clarification branch ─────────

def test_needs_clarification_forces_the_tools_own_message():
    structured = {"reply_body": "The Essential Hoodie comes in black and is very soft!"}
    result = _enforce_no_ambiguous_product_claim(
        structured,
        {"success": False, "needs_clarification": True, "message": "Which product are you asking about?"},
    )
    assert result["reply_body"] == "Which product are you asking about?"


def test_needs_clarification_false_is_a_no_op():
    structured = {"reply_body": "original"}
    result = _enforce_no_ambiguous_product_claim(structured, {"success": True})
    assert result["reply_body"] == "original"


# ── Full agent routing: pronoun follow-ups resolve against history ──────────

def _fake_ai_response(reply_body: str):
    import json
    msg = MagicMock()
    msg.content = json.dumps({"intent": "product_inquiry", "reply_body": reply_body, "risk_level": "low"})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _run_query(message: str, rec_return=None, inv_return=None):
    rec_return = rec_return or {"success": True, "no_candidates": True, "message": "no candidates"}
    inv_return = inv_return or {"success": False, "message": "not found"}
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(return_value=(_fake_ai_response("Here's what I found!"), "test_provider", "test_model", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}))), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))), \
         patch("src.agent.customer_success_agent.v3_tools.get_product_recommendations", new=AsyncMock(return_value=rec_return)) as mock_rec, \
         patch("src.agent.customer_success_agent.v3_tools.get_inventory_status", new=AsyncMock(return_value=inv_return)) as mock_inv:
        result = run(customer_success_agent.process_customer_query(
            query=message,
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1",
            store_id=None,
            ticket_id="ticket-1",
        ))
    return result, mock_rec, mock_inv


def test_show_me_that_one_resolves_against_history():
    query = (
        "[CHAT HISTORY — earlier in this conversation:]\n"
        "Customer: Do you have anything similar to the Essential Hoodie?\n"
        "Luna: Here are a few similar items.\n"
        "[END CHAT HISTORY]\n\n"
        "Customer: Show me that one again"
    )
    result, mock_rec, mock_inv = _run_query(query)
    mock_rec.assert_called_once()
    assert mock_rec.call_args.args[0] == "essential hoodie"
    mock_inv.assert_not_called()


def test_pronoun_followup_with_no_resolvable_history_asks_for_clarification_not_silence():
    """The core fix: previously this fell through to a fully ungrounded LLM
    reply with zero grounding and zero guard coverage. Now it must produce a
    deterministic clarifying question and never call the tool with a
    fabricated anchor."""
    query = (
        "[CHAT HISTORY — earlier in this conversation:]\n"
        "Customer: Hi, what's your return policy?\n"
        "Luna: You can return items within 30 days.\n"
        "[END CHAT HISTORY]\n\n"
        "Customer: What about this one?"
    )
    result, mock_rec, mock_inv = _run_query(query)
    mock_rec.assert_not_called()
    assert "which product" in result["reply_body"].lower()


def test_do_you_have_it_in_black_resolves_the_real_product_not_the_literal_phrase():
    """The other core fix: previously the entire phrase "it in black"/"this
    in another color" was passed to get_inventory_status as if it were a
    literal product name, guaranteeing an honest-but-useless "not found"."""
    query = (
        "[CHAT HISTORY — earlier in this conversation:]\n"
        "Customer: Do you have the Winter Parka in stock?\n"
        "Luna: Yes, the Winter Parka is in stock.\n"
        "[END CHAT HISTORY]\n\n"
        "Customer: Do you have it in black?"
    )
    result, mock_rec, mock_inv = _run_query(query)
    mock_inv.assert_called_once()
    assert mock_inv.call_args.args[0] == "winter parka"
    mock_rec.assert_not_called()


def test_what_about_a_smaller_size_resolves_against_history():
    query = (
        "[CHAT HISTORY — earlier in this conversation:]\n"
        "Customer: Is the Essential Hoodie available?\n"
        "Luna: Yes, the Essential Hoodie is available.\n"
        "[END CHAT HISTORY]\n\n"
        "Customer: What about a smaller size?"
    )
    result, mock_rec, mock_inv = _run_query(query)
    mock_inv.assert_called_once()
    assert mock_inv.call_args.args[0] == "essential hoodie"


def test_variant_followup_with_no_resolvable_history_asks_for_clarification_not_a_garbage_lookup():
    query = "Do you have it in another color?"  # no history at all
    result, mock_rec, mock_inv = _run_query(query)
    mock_inv.assert_not_called()
    assert "which product" in result["reply_body"].lower()


def test_same_one_in_blue_resolves_against_history():
    query = (
        "[CHAT HISTORY — earlier in this conversation:]\n"
        "Customer: Tell me about the Signature Jacket\n"
        "Luna: The Signature Jacket is a warm, water-resistant option.\n"
        "[END CHAT HISTORY]\n\n"
        "Customer: Same one in blue?"
    )
    result, mock_rec, mock_inv = _run_query(query)
    mock_inv.assert_called_once()
    assert "signature jacket" in mock_inv.call_args.args[0]
