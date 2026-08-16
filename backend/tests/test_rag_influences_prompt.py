"""
Proves company-specific RAG content actually reaches the model for
policy-shaped questions, rather than the agent answering from generic LLM
world-knowledge. This is the testable half of "RAG must actually matter" -
whether the model then reliably obeys retrieved text can't be proven without
a real LLM call, but whether the retrieved text is actually placed in front
of it can be, deterministically.

Uses a mocked brand_knowledge_service.get_brand_context() return value (a
distinctive, invented policy string), not a real production KB row - no fake
data was inserted into any real database for this test.
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


def _fake_ai_response(reply_body: str):
    msg = MagicMock()
    msg.content = json.dumps({"intent": "general_inquiry", "reply_body": reply_body, "risk_level": "low"})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


# A distinctive, made-up policy value that could not plausibly appear in the
# static prompt template or generic LLM knowledge - only in retrieved RAG
# content - so finding it in the sent prompt proves the retrieval path, not
# a coincidence.
_DISTINCTIVE_POLICY = "Returns are accepted within 63 days for CustomBrandXYZ store credit only, no cash refunds."


def _run_query_capturing_prompt(query: str, rag_context: str):
    captured = {}

    async def _capture_completion(*, messages, **kwargs):
        captured["messages"] = messages
        return _fake_ai_response("Sure, here's our policy."), "test_provider", "test_model", \
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=_capture_completion)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value=rag_context)), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))):
        run(customer_success_agent.process_customer_query(
            query=query,
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "email"},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
        ))
    return captured["messages"]


def test_company_specific_return_policy_reaches_the_system_prompt():
    messages = _run_query_capturing_prompt("What's your return policy?", _DISTINCTIVE_POLICY)
    system_content = next(m["content"] for m in messages if m["role"] == "system")
    assert _DISTINCTIVE_POLICY in system_content


def test_rag_retrieval_is_scoped_to_the_query_and_store_id():
    """get_brand_context must actually be called with the real query and
    store_id, not a placeholder - otherwise the "retrieval" would be
    decorative even if its result technically reaches the prompt."""
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(return_value=(_fake_ai_response("ok"), "p", "m", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 1, "attempts": 1}))), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")) as mock_rag, \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))):
        run(customer_success_agent.process_customer_query(
            query="What is your shipping policy?",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "email"},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
        ))

    mock_rag.assert_awaited_once_with("brand-1", "What is your shipping policy?")


def test_no_store_id_means_no_rag_call_not_an_unscoped_fallback():
    """Confirms the existing tenant-isolation guarantee is unaffected by this
    test file's mocking - no store_id means no RAG lookup runs at all, never
    an unscoped one."""
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(return_value=(_fake_ai_response("ok"), "p", "m", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 1, "attempts": 1}))), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")) as mock_rag, \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))):
        run(customer_success_agent.process_customer_query(
            query="What is your shipping policy?",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "email"},
            tenant_id="tenant-1",
            store_id=None,
            ticket_id="ticket-1",
        ))

    mock_rag.assert_not_awaited()
