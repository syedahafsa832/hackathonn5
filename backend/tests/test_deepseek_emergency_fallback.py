"""
DeepSeek Emergency Fallback Tests (Sly Mode only)
==================================================
DeepSeek is a single-tenant, last-resort fallback paid for out of a small
personal credit balance — it must never become a general provider. Covers:

1. The eligible tenant (matched by a stable tenant_id, never a brand name
   string) can reach DeepSeek, but only after every configured provider has
   already failed.
2. Any other tenant_id can NEVER invoke DeepSeek, even when every provider
   fails identically.
3. A successful primary provider means DeepSeek is never called at all.
4. The persisted invocation counter increments only on an actual DeepSeek
   request — never merely for reaching the fallback chain.
5. Once DEEPSEEK_MAX_INVOCATIONS is reached, the next attempt is blocked
   and falls through to the normal provider-exhausted handling.
6. Every existing (non-Sly-Mode) provider behavior is unaffected — no
   tenant_id, or an unrelated one, produces byte-identical behavior to the
   pre-DeepSeek code path.
"""
import os
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.ai_provider_manager import AIProviderManager, AllProvidersFailedError, _Provider

SLY_MODE_TENANT_ID = "b7f90dce-890e-4427-b20b-8cb44e5640a1"
OTHER_TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _fake_response(text="ok"):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=text))]
    resp.usage = None
    return resp


class Rate429(Exception):
    status_code = 429


def _manager_with(*labels, deepseek=False):
    """Same pattern test_ai_provider_fallback.py uses — bypasses env-var
    loading, and pre-populates _clients so _client_for() never constructs a
    real OpenAI() client (including for the deepseek_emergency provider,
    added to _clients the same way the real code would construct it)."""
    mgr = AIProviderManager.__new__(AIProviderManager)
    mgr._providers = [_Provider(label, f"key-{label}", "mistral-large-latest") for label in labels]
    mgr._clients = {}
    for label in labels:
        mgr._clients[label] = MagicMock()
    if deepseek:
        mgr._clients["deepseek_emergency"] = MagicMock()
    return mgr


def _failing(client):
    client.chat.completions.create.side_effect = Rate429("429 rate limit")


def _succeeding(client, text="ok"):
    client.chat.completions.create.return_value = _fake_response(text)


class _FakeSettingsStore:
    """In-memory stand-in for the settings table, isolated per test. Tracks
    exactly one key (DEEPSEEK_USAGE_SETTING_KEY in practice) — `get`/`set`
    ignore the key argument since these tests never touch a second one."""

    def __init__(self, initial_count: int = 0):
        self.value = {"count": initial_count} if initial_count else None

    def get(self, key):
        return self.value

    def set(self, key, value):
        self.value = value


def _patched(mgr, *, tenant_id, max_invocations=25, settings=None, sleep=True):
    settings = settings or _FakeSettingsStore()
    patches = [
        patch("src.services.ai_provider_manager.DEEPSEEK_API_KEY", "sk-deepseek-test"),
        patch("src.services.ai_provider_manager.DEEPSEEK_FALLBACK_TENANT_ID", tenant_id),
        patch("src.services.ai_provider_manager.DEEPSEEK_MAX_INVOCATIONS", max_invocations),
        patch("src.services.ai_provider_manager.supabase_get_setting", side_effect=settings.get),
        patch("src.services.ai_provider_manager.supabase_set_setting", side_effect=settings.set),
    ]
    if sleep:
        patches.append(patch("src.services.ai_provider_manager.asyncio.sleep", new=AsyncMock(return_value=None)))
    return patches, settings


async def _run_with_patches(patches, coro):
    from contextlib import ExitStack
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return await coro


# ─── 1. Sly Mode reaches DeepSeek only after every provider fails ──────────

@pytest.mark.asyncio
async def test_sly_mode_reaches_deepseek_only_after_providers_fail():
    mgr = _manager_with("primary", "fallback_1", deepseek=True)
    _failing(mgr._clients["primary"])
    _failing(mgr._clients["fallback_1"])
    _succeeding(mgr._clients["deepseek_emergency"], "deepseek saved the day")

    patches, _ = _patched(mgr, tenant_id=SLY_MODE_TENANT_ID)
    response, label, model, usage = await _run_with_patches(
        patches,
        mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}], tenant_id=SLY_MODE_TENANT_ID),
    )

    assert label == "deepseek_emergency"
    assert model == "deepseek-flash"
    assert response.choices[0].message.content == "deepseek saved the day"
    mgr._clients["primary"].chat.completions.create.assert_called_once()
    mgr._clients["fallback_1"].chat.completions.create.assert_called_once()
    mgr._clients["deepseek_emergency"].chat.completions.create.assert_called_once()


# ─── 2. A non-Sly tenant can never invoke DeepSeek ─────────────────────────

@pytest.mark.asyncio
async def test_non_sly_tenant_never_invokes_deepseek():
    mgr = _manager_with("primary", deepseek=True)
    _failing(mgr._clients["primary"])

    patches, _ = _patched(mgr, tenant_id=SLY_MODE_TENANT_ID)
    with pytest.raises(AllProvidersFailedError):
        await _run_with_patches(
            patches,
            mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}], tenant_id=OTHER_TENANT_ID),
        )

    mgr._clients["deepseek_emergency"].chat.completions.create.assert_not_called()


@pytest.mark.asyncio
async def test_no_tenant_id_at_all_never_invokes_deepseek():
    """The overwhelming majority of existing callers never pass tenant_id —
    confirms the default (None) is exactly as safe as an explicitly wrong one."""
    mgr = _manager_with("primary", deepseek=True)
    _failing(mgr._clients["primary"])

    patches, _ = _patched(mgr, tenant_id=SLY_MODE_TENANT_ID)
    with pytest.raises(AllProvidersFailedError):
        await _run_with_patches(patches, mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}]))

    mgr._clients["deepseek_emergency"].chat.completions.create.assert_not_called()


# ─── 3. Primary succeeding means DeepSeek is never called, even for Sly Mode ─

@pytest.mark.asyncio
async def test_successful_primary_never_calls_deepseek_even_for_sly_mode():
    mgr = _manager_with("primary", deepseek=True)
    _succeeding(mgr._clients["primary"], "primary reply")

    patches, _ = _patched(mgr, tenant_id=SLY_MODE_TENANT_ID)
    response, label, model, usage = await _run_with_patches(
        patches,
        mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}], tenant_id=SLY_MODE_TENANT_ID),
    )

    assert label == "primary"
    mgr._clients["deepseek_emergency"].chat.completions.create.assert_not_called()


# ─── 4. The invocation counter increments only on an actual DeepSeek call ──

@pytest.mark.asyncio
async def test_invocation_count_only_increments_on_actual_deepseek_call():
    settings = _FakeSettingsStore()

    # (a) Primary succeeds — DeepSeek never reached, count stays 0.
    mgr_a = _manager_with("primary", deepseek=True)
    _succeeding(mgr_a._clients["primary"])
    patches_a, _ = _patched(mgr_a, tenant_id=SLY_MODE_TENANT_ID, settings=settings)
    await _run_with_patches(
        patches_a,
        mgr_a.create_chat_completion(messages=[{"role": "user", "content": "hi"}], tenant_id=SLY_MODE_TENANT_ID),
    )
    assert settings.value is None  # never written

    # (b) Wrong tenant, all providers fail — DeepSeek never reached either.
    mgr_b = _manager_with("primary", deepseek=True)
    _failing(mgr_b._clients["primary"])
    patches_b, _ = _patched(mgr_b, tenant_id=SLY_MODE_TENANT_ID, settings=settings)
    with pytest.raises(AllProvidersFailedError):
        await _run_with_patches(
            patches_b,
            mgr_b.create_chat_completion(messages=[{"role": "user", "content": "hi"}], tenant_id=OTHER_TENANT_ID),
        )
    assert settings.value is None  # still never written

    # (c) Sly Mode, all providers fail — DeepSeek genuinely called, count -> 1.
    mgr_c = _manager_with("primary", deepseek=True)
    _failing(mgr_c._clients["primary"])
    _succeeding(mgr_c._clients["deepseek_emergency"])
    patches_c, _ = _patched(mgr_c, tenant_id=SLY_MODE_TENANT_ID, settings=settings)
    await _run_with_patches(
        patches_c,
        mgr_c.create_chat_completion(messages=[{"role": "user", "content": "hi"}], tenant_id=SLY_MODE_TENANT_ID),
    )
    assert settings.value["count"] == 1

    # (d) A DeepSeek call that itself fails still counts (still real spend).
    mgr_d = _manager_with("primary", deepseek=True)
    _failing(mgr_d._clients["primary"])
    _failing(mgr_d._clients["deepseek_emergency"])
    patches_d, _ = _patched(mgr_d, tenant_id=SLY_MODE_TENANT_ID, settings=settings)
    with pytest.raises(AllProvidersFailedError):
        await _run_with_patches(
            patches_d,
            mgr_d.create_chat_completion(messages=[{"role": "user", "content": "hi"}], tenant_id=SLY_MODE_TENANT_ID),
        )
    assert settings.value["count"] == 2


# ─── 5. After 25 invocations, the 26th is blocked ──────────────────────────

@pytest.mark.asyncio
async def test_26th_deepseek_invocation_is_blocked():
    settings = _FakeSettingsStore(initial_count=25)

    mgr = _manager_with("primary", deepseek=True)
    _failing(mgr._clients["primary"])
    _succeeding(mgr._clients["deepseek_emergency"])  # would succeed if ever called

    patches, _ = _patched(mgr, tenant_id=SLY_MODE_TENANT_ID, max_invocations=25, settings=settings)
    with pytest.raises(AllProvidersFailedError):
        await _run_with_patches(
            patches,
            mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}], tenant_id=SLY_MODE_TENANT_ID),
        )

    mgr._clients["deepseek_emergency"].chat.completions.create.assert_not_called()
    assert settings.value["count"] == 25  # unchanged — never incremented past the cap


@pytest.mark.asyncio
async def test_25th_invocation_still_allowed_26th_is_not():
    """Boundary check: exactly at the configured cap, one more call is still
    allowed (bringing the count to the cap); only the call after that is blocked."""
    settings = _FakeSettingsStore(initial_count=24)

    mgr = _manager_with("primary", deepseek=True)
    _failing(mgr._clients["primary"])
    _succeeding(mgr._clients["deepseek_emergency"])

    patches, _ = _patched(mgr, tenant_id=SLY_MODE_TENANT_ID, max_invocations=25, settings=settings)
    response, label, _model, _usage = await _run_with_patches(
        patches,
        mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}], tenant_id=SLY_MODE_TENANT_ID),
    )
    assert label == "deepseek_emergency"
    assert settings.value["count"] == 25


# ─── 6. Every other tenant's existing provider behavior is unchanged ──────

@pytest.mark.asyncio
async def test_existing_behavior_unchanged_when_deepseek_not_configured_at_all():
    """No DEEPSEEK_* env vars set (the real state for every tenant/deploy
    besides Sly Mode's) — behavior must be byte-identical to before this
    feature existed: same AllProvidersFailedError, same attempts list."""
    mgr = _manager_with("primary", "fallback_1")
    _failing(mgr._clients["primary"])
    _failing(mgr._clients["fallback_1"])

    with patch("src.services.ai_provider_manager.asyncio.sleep", new=AsyncMock(return_value=None)):
        with pytest.raises(AllProvidersFailedError) as exc_info:
            await mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}], tenant_id=SLY_MODE_TENANT_ID)

    assert len(exc_info.value.attempts) == 2
    assert all(a["label"] != "deepseek_emergency" for a in exc_info.value.attempts)


@pytest.mark.asyncio
async def test_existing_behavior_unchanged_for_other_tenants_when_deepseek_configured():
    """Even with DeepSeek fully configured and Sly Mode eligible, a
    different tenant's failure mode/attempts list is unaffected."""
    mgr = _manager_with("primary", "fallback_1", deepseek=True)
    _failing(mgr._clients["primary"])
    _failing(mgr._clients["fallback_1"])

    patches, _ = _patched(mgr, tenant_id=SLY_MODE_TENANT_ID)
    with pytest.raises(AllProvidersFailedError) as exc_info:
        await _run_with_patches(
            patches,
            mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}], tenant_id=OTHER_TENANT_ID),
        )

    assert len(exc_info.value.attempts) == 2
    assert all(a["label"] != "deepseek_emergency" for a in exc_info.value.attempts)
    mgr._clients["deepseek_emergency"].chat.completions.create.assert_not_called()


def test_deepseek_never_added_to_the_normal_provider_chain():
    """DeepSeek must never appear in self._providers regardless of env vars
    — it is structurally excluded from the chain every tenant shares."""
    env = {
        "MISTRAL_API_KEY": "mistral-primary-key",
        "DEEPSEEK_API_KEY": "sk-deepseek-test",
        "DEEPSEEK_FALLBACK_TENANT_ID": SLY_MODE_TENANT_ID,
    }
    with patch.dict(os.environ, env, clear=True):
        mgr = AIProviderManager.__new__(AIProviderManager)
        providers = mgr._load_providers()

    labels = [p.label for p in providers]
    assert "deepseek_emergency" not in labels
    assert labels == ["primary"]
