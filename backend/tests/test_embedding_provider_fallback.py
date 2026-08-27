"""
Embedding provider reliability (Step 5 "Test Luna" investigation follow-up).

Root cause found in the read-only investigation: brand_knowledge_service.py
and rag_engine.py each built their own single-key OpenAI client
(MISTRAL_API_KEY only, no fallback), called it *synchronously* inside an
`async def` (blocking the event loop), with no explicit timeout/retry policy
(inheriting the SDK's 600s timeout / 2 retries). Under real key exhaustion,
that could stall a single request for minutes - long past the dashboard's
35s axios timeout - even though the exact same request's chat-completion
call already had working multi-key failover via ai_provider_manager.

Fix: embeddings now go through ai_provider_manager.create_embedding(), which
rotates across every configured Mistral key (same account family, same
mistral-embed model, same vector(1024) dimension the schema expects - see
v3_rag_schema.sql / migrations 005 & 006) with a bounded 8s/no-retry policy
per attempt. Groq is deliberately excluded - it has no embeddings endpoint.
_get_embedding in both callers is now `async def` delegating straight to it,
so asyncio.to_thread wrapping isn't needed at the call sites that used it.

These tests cover the ai_provider_manager.create_embedding contract itself,
plus the two behavioral guarantees the fix depends on: an embedding outage
degrades to "no context" (never a crash, never fabricated context), and it
never affects the separate, already-working chat-completion fallback.
"""
import os
import sys
import json
import logging
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
import pytest  # noqa: E402

from src.services.ai_provider_manager import (  # noqa: E402
    AIProviderManager, _Provider, DEFAULT_EMBEDDING_MODEL, EMBEDDING_TIMEOUT_SECONDS,
)


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _manager_with(*labels_and_keys, base_urls=None):
    """Build a manager with explicit providers, bypassing env-var loading -
    same helper pattern as test_ai_provider_fallback.py."""
    mgr = AIProviderManager.__new__(AIProviderManager)
    mgr._providers = [
        _Provider(label, f"key-{label}", "mistral-large-latest", base_url=(base_urls or {}).get(label, "https://api.mistral.ai/v1"))
        for label in labels_and_keys
    ]
    mgr._clients = {}
    return mgr


def _embedding_response(vector):
    resp = MagicMock()
    resp.data = [MagicMock(embedding=vector)]
    return resp


class AuthError(Exception):
    """Mimics an OpenAI SDK auth error, which can echo back a key fragment
    in its own message - the exact leak this fix's logging must avoid."""
    status_code = 401


# ── 1. Mistral-only rotation, in provider order ──────────────────────────────

def test_normal_embedding_succeeds_on_primary():
    mgr = _manager_with("primary", "fallback_1")
    primary_client = MagicMock()
    primary_client.with_options.return_value = primary_client
    primary_client.embeddings.create.return_value = _embedding_response([0.1, 0.2, 0.3])
    mgr._clients["primary"] = primary_client
    fallback_client = MagicMock()
    mgr._clients["fallback_1"] = fallback_client

    result = run(mgr.create_embedding(text="what is your return policy?"))

    assert result == [0.1, 0.2, 0.3]
    primary_client.embeddings.create.assert_called_once()
    fallback_client.embeddings.create.assert_not_called()


def test_primary_embedding_key_failure_falls_back_to_next_mistral_key():
    mgr = _manager_with("primary", "fallback_1", "fallback_2")
    primary_client = MagicMock()
    primary_client.with_options.return_value = primary_client
    primary_client.embeddings.create.side_effect = AuthError("quota exceeded")
    mgr._clients["primary"] = primary_client

    fallback_client = MagicMock()
    fallback_client.with_options.return_value = fallback_client
    fallback_client.embeddings.create.return_value = _embedding_response([0.4, 0.5])
    mgr._clients["fallback_1"] = fallback_client

    unused_client = MagicMock()
    mgr._clients["fallback_2"] = unused_client

    result = run(mgr.create_embedding(text="hello"))

    assert result == [0.4, 0.5]
    primary_client.embeddings.create.assert_called_once()
    fallback_client.embeddings.create.assert_called_once()
    # Bounded: stops at the first success, never tries a third key.
    unused_client.embeddings.create.assert_not_called()


def test_groq_is_never_tried_for_embeddings():
    """Groq has no embeddings endpoint - even though it's a valid chat
    fallback, it must be excluded from the embedding rotation entirely, not
    just skipped silently after being attempted and failing."""
    mgr = _manager_with("primary", "groq_fallback_1")
    primary_client = MagicMock()
    primary_client.with_options.return_value = primary_client
    primary_client.embeddings.create.side_effect = AuthError("quota exceeded")
    mgr._clients["primary"] = primary_client

    groq_client = MagicMock()
    mgr._clients["groq_fallback_1"] = groq_client

    result = run(mgr.create_embedding(text="hello"))

    assert result is None
    groq_client.embeddings.create.assert_not_called()
    groq_client.with_options.assert_not_called()


def test_mistral_providers_property_excludes_groq_labels():
    mgr = _manager_with("primary", "fallback_1", "groq_fallback_1", "groq_fallback_2")
    assert [p.label for p in mgr.mistral_providers] == ["primary", "fallback_1"]


# ── 2. All keys failing degrades to None, never raises ───────────────────────

def test_all_mistral_keys_failing_returns_none_not_raise():
    mgr = _manager_with("primary", "fallback_1")
    for label in ("primary", "fallback_1"):
        client = MagicMock()
        client.with_options.return_value = client
        client.embeddings.create.side_effect = AuthError("rate limited")
        mgr._clients[label] = client

    result = run(mgr.create_embedding(text="hello"))

    assert result is None


def test_no_mistral_keys_configured_returns_none_immediately():
    mgr = _manager_with()  # no keys at all
    result = run(mgr.create_embedding(text="hello"))
    assert result is None


# ── 3. Bounded, retry-free per attempt (the actual latency fix) ─────────────

def test_embedding_call_disables_sdk_retries_and_passes_bounded_timeout():
    """Each attempt must ask for max_retries=0 (our own key rotation is the
    retry strategy) and an explicit bounded timeout - never the SDK's
    600s-timeout/2-retry default that let one exhausted key stall a whole
    request for minutes."""
    mgr = _manager_with("primary")
    raw_client = MagicMock()
    bounded_client = MagicMock()
    raw_client.with_options.return_value = bounded_client
    bounded_client.embeddings.create.return_value = _embedding_response([0.1])
    mgr._clients["primary"] = raw_client

    run(mgr.create_embedding(text="hello"))

    raw_client.with_options.assert_called_once_with(max_retries=0)
    _, kwargs = bounded_client.embeddings.create.call_args
    assert kwargs["model"] == DEFAULT_EMBEDDING_MODEL
    assert kwargs["timeout"] == EMBEDDING_TIMEOUT_SECONDS


def test_embedding_timeout_is_bounded_not_unbounded():
    """Regression guard for the literal number: it must be a small, sane
    bound, not accidentally left at (or restored to) an SDK-default-sized
    value that would defeat the whole point of this fix."""
    assert 0 < EMBEDDING_TIMEOUT_SECONDS <= 15


# ── 4. No secrets ever reach the logs on a failure ──────────────────────────

def test_embedding_failure_logs_never_contain_the_raw_exception_text(caplog):
    """An auth/invalid-key error's own message can echo back a key fragment
    (e.g. "Incorrect API key provided: key-primary..."). The classified
    reason (_describe) must be logged instead of str(exception)."""
    mgr = _manager_with("primary")
    client = MagicMock()
    client.with_options.return_value = client
    secret_looking_text = "Incorrect API key provided: key-primary-SECRETVALUE123"
    client.embeddings.create.side_effect = AuthError(secret_looking_text)
    mgr._clients["primary"] = client

    with caplog.at_level(logging.WARNING, logger="src.services.ai_provider_manager"):
        result = run(mgr.create_embedding(text="hello"))

    assert result is None
    log_text = "\n".join(r.message for r in caplog.records)
    assert "SECRETVALUE123" not in log_text
    assert "key-primary" not in log_text
    assert "reason=" in log_text


# ── 5. Embedding failure never touches/consumes the chat-completion path ────

@pytest.mark.asyncio
async def test_embedding_outage_does_not_affect_chat_completion_fallback():
    """The two paths must stay independent: every embedding key failing
    must not raise, retry, or otherwise interfere with create_chat_completion
    on the very same manager instance right after."""
    mgr = _manager_with("primary", "fallback_1")
    embed_client = MagicMock()
    embed_client.with_options.return_value = embed_client
    embed_client.embeddings.create.side_effect = AuthError("quota exceeded")
    mgr._clients["primary"] = embed_client

    embedding = await mgr.create_embedding(text="hello")
    assert embedding is None

    # Same "primary" client, now used for a chat completion - proves
    # with_options()/max_retries=0 on the embedding call didn't mutate the
    # shared client object used for chat.
    chat_response = MagicMock()
    chat_response.choices = [MagicMock(message=MagicMock(content="a real reply"))]
    chat_response.usage = None
    embed_client.chat.completions.create.return_value = chat_response

    response, label, _model, _usage = await mgr.create_chat_completion(
        messages=[{"role": "user", "content": "hi"}]
    )
    assert label == "primary"
    assert response.choices[0].message.content == "a real reply"


# ── 6. brand_knowledge_service degrades gracefully, never crashes ──────────

def test_get_brand_context_returns_empty_string_when_all_embeddings_fail():
    from src.services.brand_knowledge_service import BrandKnowledgeService

    svc = BrandKnowledgeService()
    with patch("src.services.ai_provider_manager.ai_provider_manager.create_embedding", new=AsyncMock(return_value=None)):
        context = run(svc.get_brand_context(brand_id="brand-1", query="where is my order?"))

    assert context == ""


# ── 7. Full production path: Step 5 → RAG/embedding → agent → provider
# manager → response. A total embedding outage must not stop a normal chat
# reply from generating - RAG context just comes back empty, and everything
# downstream (intent detection, chat completion, reply) proceeds exactly as
# if there were no knowledge base at all. Confirms this is not the
# provider_outage/escalation path - that's a separate, already-covered
# failure mode (test_provider_outage_no_auto_fallback.py) triggered only by
# the *chat* provider manager, not embeddings.

def _fake_ai_response(reply_body: str):
    msg = MagicMock()
    msg.content = json.dumps({"intent": "general_inquiry", "reply_body": reply_body, "risk_level": "low"})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def test_process_customer_query_completes_normally_when_embedding_fails():
    from src.agent.customer_success_agent import customer_success_agent
    from src.services.intent_detector import IntentResult

    no_action = IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm")
    fake_usage = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 1, "attempts": 1}

    with patch("src.services.ai_provider_manager.ai_provider_manager.create_embedding", new=AsyncMock(return_value=None)), \
         patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion",
               new=AsyncMock(return_value=(_fake_ai_response("Sure, happy to help with that!"), "test_provider", "test_model", fake_usage))), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=no_action)), \
         patch("src.lib.supabase_client.supabase_select", return_value=[]):
        result = run(customer_success_agent.process_customer_query(
            query="can you help me?",
            customer_info={"name": "Sam", "email": "sam@example.com", "channel": "chat"},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
        ))

    # A real chat reply still comes back - the embedding outage never
    # escalated this into the provider_outage/no-auto-fallback path.
    assert "Sure, happy to help with that!" in result["reply_body"]
    assert result.get("provider_outage") is not True
    assert result.get("ai_reply_generated") is not False
