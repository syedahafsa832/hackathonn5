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
    assert groq.model == "llama-3.1-8b-instant"


def test_load_providers_inserts_openrouter_between_mistral_and_groq():
    """Recommended fallback chain: Mistral primary -> OpenRouter (two free
    models on one key) -> Groq. Confirms the exact requested order and that
    both OpenRouter tiers share the one configured key."""
    env = {
        "MISTRAL_API_KEY": "mistral-primary-key",
        "OPENROUTER_API_KEY": "sk-or-test-key",
        "GROQ_API_KEY": "gsk_test_key",
    }
    with patch.dict(os.environ, env, clear=True):
        mgr = AIProviderManager.__new__(AIProviderManager)
        providers = mgr._load_providers()

    labels = [p.label for p in providers]
    assert labels == ["primary", "openrouter_fallback_1", "openrouter_fallback_2", "groq_fallback_1"]
    or1, or2 = providers[1], providers[2]
    assert or1.api_key == "sk-or-test-key"
    assert or2.api_key == "sk-or-test-key"
    assert or1.base_url == "https://openrouter.ai/api/v1"
    assert or2.base_url == "https://openrouter.ai/api/v1"
    assert or1.model == "mistralai/mistral-nemo:free"
    assert or2.model == "meta-llama/llama-3.3-70b-instruct:free"


def test_load_providers_omits_openrouter_when_unset():
    env = {"MISTRAL_API_KEY": "mistral-primary-key", "GROQ_API_KEY": "gsk_test_key"}
    with patch.dict(os.environ, env, clear=True):
        mgr = AIProviderManager.__new__(AIProviderManager)
        providers = mgr._load_providers()

    assert [p.label for p in providers] == ["primary", "groq_fallback_1"]


def test_openrouter_model_fallbacks_are_individually_overridable():
    env = {
        "MISTRAL_API_KEY": "mistral-primary-key",
        "OPENROUTER_API_KEY": "sk-or-test-key",
        "OPENROUTER_MODEL_FALLBACK_1": "custom/model-a:free",
    }
    with patch.dict(os.environ, env, clear=True):
        mgr = AIProviderManager.__new__(AIProviderManager)
        providers = mgr._load_providers()

    or1, or2 = providers[1], providers[2]
    assert or1.model == "custom/model-a:free"
    # Only fallback_1 was overridden - fallback_2 keeps its own default.
    assert or2.model == "meta-llama/llama-3.3-70b-instruct:free"


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
async def test_full_chain_falls_back_mistral_to_openrouter_to_groq():
    """End-to-end proof of the recommended chain: Mistral primary fails,
    both OpenRouter tiers fail, Groq finally succeeds - one response, every
    intermediate provider tried exactly once, in order."""
    mgr = _manager_with("primary")
    mgr._providers.append(_Provider("openrouter_fallback_1", "sk-or-test", "mistralai/mistral-nemo:free", base_url="https://openrouter.ai/api/v1"))
    mgr._providers.append(_Provider("openrouter_fallback_2", "sk-or-test", "meta-llama/llama-3.3-70b-instruct:free", base_url="https://openrouter.ai/api/v1"))
    mgr._providers.append(_Provider("groq_fallback_1", "gsk_test", "llama-3.1-8b-instant", base_url="https://api.groq.com/openai/v1"))
    for label in ("primary", "openrouter_fallback_1", "openrouter_fallback_2"):
        client = MagicMock()
        client.chat.completions.create.side_effect = Rate429("429 rate limit")
        mgr._clients[label] = client
    groq_client = MagicMock()
    groq_client.chat.completions.create.return_value = _fake_response("groq reply")
    mgr._clients["groq_fallback_1"] = groq_client

    with patch("src.services.ai_provider_manager.asyncio.sleep", new=AsyncMock(return_value=None)):
        response, label, model, usage = await mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}])

    assert label == "groq_fallback_1"
    assert model == "llama-3.1-8b-instant"
    assert response.choices[0].message.content == "groq reply"
    assert usage["attempts"] == 4
    for prior_label in ("primary", "openrouter_fallback_1", "openrouter_fallback_2"):
        mgr._clients[prior_label].chat.completions.create.assert_called_once()


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
    escalation — not the generic 'System error: ...' the fallback path uses.

    Customer-facing text is a separate, explicit opt-in
    (send_customer_fallback / brands.provider_outage_fallback_enabled):
    by default, no reply is auto-sent during a provider outage — the
    message is still saved and the ticket still escalates (asserted here),
    but nothing goes out claiming a human is already on it unless the
    merchant turned that on."""
    from src.agent.customer_success_agent import CustomerSuccessAgent
    result = CustomerSuccessAgent._get_provider_failure_response(
        MagicMock(), brand_name="Acme", agent_name="Luna"
    )
    assert result["escalate"] is True
    assert result["provider_outage"] is True
    assert result["status"] == "escalated"
    # Plain-language for a non-technical store owner reading the Escalations
    # list — must name the actual cause (quota), not vague infra jargon.
    assert "quota" in result["escalation_reason"].lower()
    assert "system error" not in result["escalation_reason"].lower()
    # Default: no customer-facing reply at all - never Luna's own wording
    # for a request that was never actually processed.
    assert result["reply_body"] == ""
    # A failed AI call must never consume trial/plan quota — callers
    # (message_processor.py, v2_chat_widget.py) gate record_ai_reply_event()
    # on this flag being truthy, never on reply_body alone.
    assert result["ai_reply_generated"] is False


def test_provider_failure_with_customer_fallback_enabled_sends_the_generic_message():
    """When the merchant has explicitly opted in
    (provider_outage_fallback_enabled), a fixed, deliberately generic
    placeholder is sent instead of nothing — but the escalation/outage
    signals are unchanged, so a human is still guaranteed to review it."""
    from src.agent.customer_success_agent import CustomerSuccessAgent
    result = CustomerSuccessAgent._get_provider_failure_response(
        MagicMock(), brand_name="Acme", agent_name="Luna", send_customer_fallback=True,
    )
    assert result["escalate"] is True
    assert result["provider_outage"] is True
    assert result["ai_reply_generated"] is False
    assert "reviewing it now" in result["reply_body"]
    assert "- Luna" in result["reply_body"]
    assert "Acme" in result["reply_body"]
    # Must stay generic enough to fit any request type - never a specific
    # claim like the old "I've flagged this for my team" wording.
    assert "flagged" not in result["reply_body"].lower()


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
