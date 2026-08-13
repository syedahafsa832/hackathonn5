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


def _fake_response(text="ok", usage=None):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=text))]
    if usage is not None:
        resp.usage = MagicMock(**usage)
    else:
        resp.usage = None
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

    response, label, model, usage = await mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}])

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
        response, label, model, usage = await mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}])

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


def test_load_providers_appends_groq_last_with_its_own_base_url():
    """GROQ_API_KEY should load as a final fallback after every Mistral key,
    using Groq's own base_url/model — not Mistral's DEFAULT_BASE_URL.

    clear=True (full environment swap, not merge) is required here: several
    modules imported transitively across the test suite (e.g.
    src/lib/supabase_client.py) call load_dotenv() at import time, which
    loads the real backend/.env — including real Mistral/Groq keys — into
    this process's os.environ exactly once. That leaks into any test that
    only *adds* keys on top of the existing environment (clear=False)."""
    env = {
        "MISTRAL_API_KEY": "mistral-primary-key",
        "MISTRAL_API_KEY_FALLBACK_1": "mistral-fallback-key",
        "GROQ_API_KEY": "gsk_test_key",
    }
    with patch.dict(os.environ, env, clear=True):
        mgr = AIProviderManager.__new__(AIProviderManager)
        providers = mgr._load_providers()

    labels = [p.label for p in providers]
    assert labels == ["primary", "fallback_1", "groq_fallback_1"]
    groq = providers[-1]
    assert groq.api_key == "gsk_test_key"
    assert groq.base_url == "https://api.groq.com/openai/v1"
    assert groq.model == "openai/gpt-oss-20b"


def test_load_providers_omits_groq_when_unset():
    env = {"MISTRAL_API_KEY": "mistral-primary-key"}
    with patch.dict(os.environ, env, clear=True):
        mgr = AIProviderManager.__new__(AIProviderManager)
        providers = mgr._load_providers()

    assert [p.label for p in providers] == ["primary"]


def test_load_providers_appends_second_groq_key_as_its_own_fallback():
    """A second Groq key (GROQ_API_KEY_FALLBACK_1) must load as an additional,
    independently-triable provider after the first Groq key — not replace it."""
    env = {
        "MISTRAL_API_KEY": "mistral-primary-key",
        "GROQ_API_KEY": "gsk_first_key",
        "GROQ_API_KEY_FALLBACK_1": "gsk_second_key",
    }
    with patch.dict(os.environ, env, clear=True):
        mgr = AIProviderManager.__new__(AIProviderManager)
        providers = mgr._load_providers()

    labels = [p.label for p in providers]
    assert labels == ["primary", "groq_fallback_1", "groq_fallback_2"]
    assert providers[1].api_key == "gsk_first_key"
    assert providers[2].api_key == "gsk_second_key"
    assert providers[2].base_url == "https://api.groq.com/openai/v1"


def test_client_for_uses_each_providers_own_base_url():
    """Regression guard: _client_for used to hard-code Mistral's DEFAULT_BASE_URL
    for every provider. A Groq provider must get an OpenAI client pointed at
    Groq's base_url, not Mistral's."""
    mgr = AIProviderManager.__new__(AIProviderManager)
    mgr._providers = []
    mgr._clients = {}
    groq_provider = _Provider("groq_fallback_1", "gsk_test", "openai/gpt-oss-20b", base_url="https://api.groq.com/openai/v1")

    client = mgr._client_for(groq_provider)

    assert str(client.base_url).rstrip("/") == "https://api.groq.com/openai/v1"


@pytest.mark.asyncio
async def test_mistral_exhausted_falls_back_to_groq():
    mgr = _manager_with("primary", "fallback_1")
    mgr._providers.append(_Provider("groq_fallback_1", "gsk_test", "openai/gpt-oss-20b", base_url="https://api.groq.com/openai/v1"))
    for label in ("primary", "fallback_1"):
        client = MagicMock()
        client.chat.completions.create.side_effect = Rate429("429 rate limit")
        mgr._clients[label] = client
    groq_client = MagicMock()
    groq_client.chat.completions.create.return_value = _fake_response("groq reply")
    mgr._clients["groq_fallback_1"] = groq_client

    with patch("src.services.ai_provider_manager.asyncio.sleep", new=AsyncMock(return_value=None)):
        response, label, model, usage = await mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}])

    assert label == "groq_fallback_1"
    assert response.choices[0].message.content == "groq reply"
    mgr._clients["primary"].chat.completions.create.assert_called_once()
    mgr._clients["fallback_1"].chat.completions.create.assert_called_once()


def test_provider_failure_escalation_copy():
    """The agent's total-failure path must produce a specific, actionable
    escalation — not the generic 'System error: ...' the fallback path uses."""
    from src.agent.customer_success_agent import CustomerSuccessAgent
    result = CustomerSuccessAgent._get_provider_failure_response(
        MagicMock(), brand_name="Acme", agent_name="Luna"
    )
    assert result["escalate"] is True
    assert result["provider_outage"] is True
    # Plain-language for a non-technical store owner reading the Escalations
    # list — must name the actual cause (quota), not vague infra jargon.
    assert "quota" in result["escalation_reason"].lower()
    assert "system error" not in result["escalation_reason"].lower()
    assert result["reply_body"]  # customer still gets an acknowledgment, not a blank reply
    # A failed AI call must never consume trial/plan quota — callers
    # (message_processor.py, v2_chat_widget.py) gate record_ai_reply_event()
    # on this flag being present and truthy, not on reply_body alone (which
    # is always non-empty here, since the customer still gets a canned
    # acknowledgment).
    assert "ai_reply_generated" not in result


@pytest.mark.asyncio
async def test_usage_is_captured_from_a_successful_response():
    mgr = _manager_with("primary")
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        "hi", usage={"prompt_tokens": 500, "completion_tokens": 120, "total_tokens": 620}
    )
    mgr._clients["primary"] = client

    _response, label, _model, usage = await mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}])

    assert label == "primary"
    assert usage["prompt_tokens"] == 500
    assert usage["completion_tokens"] == 120
    assert usage["total_tokens"] == 620
    assert usage["attempts"] == 1
    assert usage["provider"] == "primary"
    assert isinstance(usage["latency_ms"], int)


@pytest.mark.asyncio
async def test_usage_is_none_not_zero_when_provider_omits_it():
    """A provider response with no usage block must report None for each
    token field - never fabricate 0, which would be indistinguishable from a
    genuinely free/zero-token call."""
    mgr = _manager_with("primary")
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response("hi", usage=None)
    mgr._clients["primary"] = client

    _response, _label, _model, usage = await mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}])

    assert usage["prompt_tokens"] is None
    assert usage["completion_tokens"] is None
    assert usage["total_tokens"] is None


@pytest.mark.asyncio
async def test_usage_reflects_the_successful_attempt_after_failover_not_the_failed_one():
    """When the primary fails and the fallback succeeds, usage/attempts must
    describe the whole call (2 attempts) using the fallback's real token
    counts - not the primary's (which never returned any) and not just '1
    attempt' as if failover never happened."""
    mgr = _manager_with("primary", "fallback_1")
    primary_client = MagicMock()
    primary_client.chat.completions.create.side_effect = Rate429("429 rate limit")
    mgr._clients["primary"] = primary_client
    fallback_client = MagicMock()
    fallback_client.chat.completions.create.return_value = _fake_response(
        "fallback reply", usage={"prompt_tokens": 300, "completion_tokens": 80, "total_tokens": 380}
    )
    mgr._clients["fallback_1"] = fallback_client

    with patch("src.services.ai_provider_manager.asyncio.sleep", new=AsyncMock(return_value=None)):
        _response, label, _model, usage = await mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}])

    assert label == "fallback_1"
    assert usage["attempts"] == 2
    assert usage["provider"] == "fallback_1"
    assert usage["total_tokens"] == 380


def test_fallback_response_also_excluded_from_quota():
    """_get_fallback_response (empty API response / JSON parse error / any
    other exception) returns the same kind of canned reply_body as the
    provider-failure path — it must be excluded from quota consumption too,
    not just the provider_outage case."""
    from src.agent.customer_success_agent import CustomerSuccessAgent
    result = CustomerSuccessAgent._get_fallback_response(
        MagicMock(), "Empty API response", brand_name="Acme", agent_name="Luna"
    )
    assert result["reply_body"]
    assert "ai_reply_generated" not in result
