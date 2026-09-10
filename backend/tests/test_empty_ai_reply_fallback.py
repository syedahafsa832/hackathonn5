"""
P0: "hello" produced an AI ticket with an empty reply body.

Root cause: customer_success_agent.py builds the customer-facing reply from
the model's own structured["reply_body"] with no floor - if the model
returns reply_body="" (observed for low-content messages like "hello"),
that empty string flows straight through to reply_body / the saved
message / the dashboard.

Fix: a fallback greeting fills in whenever the generated reply is empty.
Unconditional on escalate - confirmed live (ticket #4e73b513) that an
escalated ticket's reply_body still gets emailed to the customer, so an
earlier version of this fix that skipped escalated tickets still let a
real, sent email go out with just the signature.
"""
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent  # noqa: E402
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


def _empty_reply_response(escalate=False):
    msg = MagicMock()
    msg.content = json.dumps({"intent": "greeting", "reply_body": "", "risk_level": "low", "escalate": escalate})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _run(query, escalate):
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion",
               new=AsyncMock(return_value=(_empty_reply_response(escalate), "test_provider", "test_model",
                                            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}))), \
         patch("src.services.intent_detector.intent_detector.detect",
               new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))):
        return run(customer_success_agent.process_customer_query(
            query=query,
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "email"},
            tenant_id="tenant-1", store_id=None, ticket_id="ticket-1",
        ))


def test_hello_never_produces_an_empty_reply_body():
    result = _run("hello", escalate=False)
    assert result.get("reply_body", "").strip() != "", "reply_body must never be empty for a normal, non-escalated reply"


def test_escalated_ticket_never_produces_an_empty_reply_body():
    """Regression for ticket #4e73b513: escalate=True still resulted in a
    real, sent customer email - the fallback must not skip this case."""
    result = _run("hey yo luna, do you have the black maxi dress in stock?", escalate=True)
    assert result.get("reply_body", "").strip() != "", "reply_body must never be empty even when escalating"
