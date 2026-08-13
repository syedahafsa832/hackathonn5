"""
AI usage instrumentation (tResolv economics work).

ai_provider_manager.create_chat_completion() now returns a 4th value - a
usage dict (prompt/completion/total tokens, latency_ms, attempts, provider,
model) - built from the real provider response's `.usage` block, never
fabricated. process_customer_query() threads this through as
structured["ai_usage"]; BrandMessageProcessor._log_conversation() (the one
existing ai_conversations write path) persists it into the
tokens_used/latency_ms columns that already existed but were always NULL.

These tests cover only the persistence step (_log_conversation) - the
provider-layer usage capture itself is covered by
test_ai_provider_fallback.py.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.workers.brand_message_processor import BrandMessageProcessor  # noqa: E402


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_log_conversation_persists_tokens_and_latency_when_usage_available():
    proc = BrandMessageProcessor()
    usage = {"prompt_tokens": 500, "completion_tokens": 120, "total_tokens": 620, "latency_ms": 842, "attempts": 1}

    with patch("src.workers.brand_message_processor.supabase_insert") as mock_insert:
        run(proc._log_conversation(
            brand_id="brand-1", ticket_id="ticket-1",
            user_message="hi", ai_response="hello there",
            model="mistral-large-latest", usage=usage,
        ))

    assert mock_insert.call_count == 2
    assistant_call = mock_insert.call_args_list[1]
    row = assistant_call.args[1]
    assert row["role"] == "assistant"
    assert row["tokens_used"] == 620
    assert row["latency_ms"] == 842
    assert row["model"] == "mistral-large-latest"


def test_log_conversation_leaves_tokens_unset_not_zero_when_usage_missing():
    """No usage at all (e.g. provider omitted it, or every provider failed) -
    must not write tokens_used=0, which would be indistinguishable from a
    genuinely free/zero-token call."""
    proc = BrandMessageProcessor()

    with patch("src.workers.brand_message_processor.supabase_insert") as mock_insert:
        run(proc._log_conversation(
            brand_id="brand-1", ticket_id="ticket-1",
            user_message="hi", ai_response="hello there",
            model="mistral-large-latest", usage=None,
        ))

    assistant_call = mock_insert.call_args_list[1]
    row = assistant_call.args[1]
    assert "tokens_used" not in row
    assert "latency_ms" not in row


def test_log_conversation_leaves_tokens_unset_when_provider_response_had_no_usage_block():
    """usage dict present (call succeeded) but its token fields are None
    (provider's response had no usage block) - same rule as the fully-missing
    case: never write 0."""
    proc = BrandMessageProcessor()
    usage = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 300, "attempts": 1}

    with patch("src.workers.brand_message_processor.supabase_insert") as mock_insert:
        run(proc._log_conversation(
            brand_id="brand-1", ticket_id="ticket-1",
            user_message="hi", ai_response="hello there",
            model="mistral-large-latest", usage=usage,
        ))

    assistant_call = mock_insert.call_args_list[1]
    row = assistant_call.args[1]
    assert "tokens_used" not in row
    assert row["latency_ms"] == 300  # latency is always known even when tokens aren't


def test_log_conversation_user_message_row_is_unaffected_by_usage():
    """Usage describes the AI's response, not the customer's message - the
    user-role row must never get tokens_used/latency_ms."""
    proc = BrandMessageProcessor()
    usage = {"prompt_tokens": 500, "completion_tokens": 120, "total_tokens": 620, "latency_ms": 842, "attempts": 1}

    with patch("src.workers.brand_message_processor.supabase_insert") as mock_insert:
        run(proc._log_conversation(
            brand_id="brand-1", ticket_id="ticket-1",
            user_message="hi", ai_response="hello there",
            model="mistral-large-latest", usage=usage,
        ))

    user_call = mock_insert.call_args_list[0]
    row = user_call.args[1]
    assert row["role"] == "user"
    assert "tokens_used" not in row
    assert "latency_ms" not in row
