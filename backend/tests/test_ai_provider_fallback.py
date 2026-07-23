"""
AI Provider Fallback Tests
==========================
Verifies the Mistral key failover behavior in ai_provider_manager.py:
1. Primary key works -> response generated normally, no fallback used.
2. Primary key rate-limited (429) -> fallback key is used automatically.
3. All keys fail -> AllProvidersFailedError raised with per-provider reasons,
   and the agent turns that into a clear "AI providers temporarily unavailable"
   escalation instead of a generic "System error: ...".
4. Exactly one successful response is ever returned per call, even when
   multiple providers were attempted -> no duplicate AI messages downstream.
"""
import os
import sys
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.ai_provider_manager import AIProviderManager, AllProvidersFailedError, _Provider


def _fake_response(text="ok"):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=text))]
    return resp


def _manager_with(*labels_and_keys):
    """Build a manager with explicit providers, bypassing env-var loading."""
    mgr = AIProviderManager.__new__(AIProviderManager)
    mgr._providers = [_Provider(label, f"key-{label}", "mistral-large-latest") for label in labels_and_keys]
    mgr._clients = {}
    return mgr


class Rate429(Exception):
    status_code = 429


@pytest.mark.asyncio
async def test_primary_key_works_no_fallback():
    mgr = _manager_with("primary", "fallback_1")
    primary_client = MagicMock()
    primary_client.chat.completions.create.return_value = _fake_response("primary reply")
    mgr._clients["primary"] = primary_client
    fallback_client = MagicMock()
    mgr._clients["fallback_1"] = fallback_client

    response, label, model = await mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}])

    assert label == "primary"
    assert response.choices[0].message.content == "primary reply"
    primary_client.chat.completions.create.assert_called_once()
    fallback_client.chat.completions.create.assert_not_called()


@pytest.mark.asyncio
async def test_primary_rate_limited_falls_back():
    mgr = _manager_with("primary", "fallback_1")
    primary_client = MagicMock()
    primary_client.chat.completions.create.side_effect = Rate429("429 rate limit")
    mgr._clients["primary"] = primary_client
    fallback_client = MagicMock()
    fallback_client.chat.completions.create.return_value = _fake_response("fallback reply")
    mgr._clients["fallback_1"] = fallback_client

    with patch("src.services.ai_provider_manager.asyncio.sleep", new=AsyncMock(return_value=None)):
        response, label, model = await mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}])

    assert label == "fallback_1"
    assert response.choices[0].message.content == "fallback reply"
    # Exactly one provider succeeded and exactly one response is returned —
    # nothing downstream could ever see two AI replies for this call.
    primary_client.chat.completions.create.assert_called_once()
    fallback_client.chat.completions.create.assert_called_once()


@pytest.mark.asyncio
async def test_all_providers_fail_raises_with_reasons():
    mgr = _manager_with("primary", "fallback_1", "fallback_2")
    for label in ("primary", "fallback_1", "fallback_2"):
        client = MagicMock()
        client.chat.completions.create.side_effect = Rate429("429 rate limit")
        mgr._clients[label] = client

    with patch("src.services.ai_provider_manager.asyncio.sleep", new=AsyncMock(return_value=None)):
        with pytest.raises(AllProvidersFailedError) as exc_info:
            await mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}])

    assert len(exc_info.value.attempts) == 3
    assert all(a["reason"] == "rate_limited" for a in exc_info.value.attempts)
    # Bounded retries: exactly one attempt per configured provider, never infinite.
    for label in ("primary", "fallback_1", "fallback_2"):
        mgr._clients[label].chat.completions.create.assert_called_once()


@pytest.mark.asyncio
async def test_no_providers_configured_fails_fast():
    mgr = _manager_with()  # no keys at all
    with pytest.raises(AllProvidersFailedError):
        await mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}])


def test_provider_failure_escalation_copy():
    """The agent's total-failure path must produce a specific, actionable
    escalation — not the generic 'System error: ...' the fallback path uses."""
    from src.agent.customer_success_agent import CustomerSuccessAgent
    result = CustomerSuccessAgent._get_provider_failure_response(
        MagicMock(), brand_name="Acme", agent_name="Luna"
    )
    assert result["escalate"] is True
    assert result["escalation_reason"] == "AI providers temporarily unavailable"
    assert "system error" not in result["escalation_reason"].lower()
    assert result["reply_body"]  # customer still gets an acknowledgment, not a blank reply
