"""
AfterShip Tracking & Webhook Reliability Tests
================================================
Covers Phase 4: the canonical tracking_service.py request/response path
(timeouts, malformed responses, checkpoint history, timeline rendering),
the webhook's dual-env-var secret resolution and signature verification,
and the content-hash idempotency fix that replaced the old PID-based
fallback. All AfterShip/DB calls are mocked — no live services required.
"""
import base64
import hashlib
import hmac
import importlib
import inspect
import os
import sys
import time

import httpx
import pytest
from fastapi import BackgroundTasks, HTTPException
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import src.services.tracking_service as tracking_mod  # noqa: E402
from src.services.tracking_service import get_tracking_status, build_tracking_context, get_tracking_stats  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_tracking_module_state():
    """The cache/cooldown/stats added in the production-hardening pass are
    module-level in-process state (by design — see tracking_service.py's
    module docstring). Reset it around every test so tests don't leak into
    each other."""
    tracking_mod._cache.clear()
    tracking_mod.stats.clear()
    tracking_mod._consecutive_failures = 0
    tracking_mod._cooldown_until = 0.0
    yield
    tracking_mod._cache.clear()
    tracking_mod.stats.clear()
    tracking_mod._consecutive_failures = 0
    tracking_mod._cooldown_until = 0.0


# ─── Fakes for httpx.AsyncClient ─────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


class _FakeAsyncClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        if self._exc:
            raise self._exc
        return self._response


def _client_factory(response=None, exc=None):
    def _factory(*args, **kwargs):
        return _FakeAsyncClient(response=response, exc=exc)
    return _factory


def _patch_client(response=None, exc=None):
    return patch("src.services.tracking_service.httpx.AsyncClient",
                 side_effect=_client_factory(response=response, exc=exc))


def _counting_client_factory(response=None, exc=None):
    """Like _client_factory, but tracks how many times a client was constructed
    — i.e. how many real HTTP attempts were made (used to prove caching/cooldown
    actually skip the network, and that retries happen the expected number of times)."""
    calls = {"n": 0}

    def _factory(*args, **kwargs):
        calls["n"] += 1
        return _FakeAsyncClient(response=response, exc=exc)

    _factory.calls = calls
    return _factory


def _sequenced_client_factory(*outcomes):
    """Each outcome is a (response, exc) pair. Advances through them on each
    successive client construction; the last outcome repeats once exhausted."""
    calls = {"n": 0}

    def _factory(*args, **kwargs):
        idx = min(calls["n"], len(outcomes) - 1)
        calls["n"] += 1
        response, exc = outcomes[idx]
        return _FakeAsyncClient(response=response, exc=exc)

    _factory.calls = calls
    return _factory


# ─── 1. Timeout / failure handling ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_timeout_returns_none_gracefully():
    with _patch_client(exc=httpx.TimeoutException("timed out")):
        result = await get_tracking_status("TRACK1", "dhl", "key")
    assert result is None


@pytest.mark.asyncio
async def test_tracking_api_5xx_failure_returns_none_gracefully():
    with _patch_client(response=_FakeResponse(500)):
        result = await get_tracking_status("TRACK1", "dhl", "key")
    assert result is None


@pytest.mark.asyncio
async def test_missing_or_empty_api_key_results_in_graceful_unauthorized_handling():
    with _patch_client(response=_FakeResponse(401)):
        result = await get_tracking_status("TRACK1", "dhl", "")
    assert result is None


@pytest.mark.asyncio
async def test_malformed_api_response_shape_handled_gracefully():
    resp = _FakeResponse(200, json_data={"unexpected": "shape"})
    with _patch_client(response=resp):
        result = await get_tracking_status("TRACK1", "dhl", "key")
    assert result is not None
    assert result["status"] is None
    assert result["recent_checkpoints"] == []


@pytest.mark.asyncio
async def test_missing_carrier_slug_handled_gracefully():
    resp = _FakeResponse(200, json_data={"data": {"tracking": {"tag": "InTransit", "checkpoints": []}}})
    with _patch_client(response=resp):
        result = await get_tracking_status("TRACK1", "dhl", "key")
    assert result["carrier_slug"] is None


# ─── 2. Checkpoint history / timeline ────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_checkpoint_list_produces_no_timeline():
    resp = _FakeResponse(200, json_data={"data": {"tracking": {"tag": "InTransit", "checkpoints": []}}})
    with _patch_client(response=resp):
        result = await get_tracking_status("TRACK1", "dhl", "key")
    assert result["recent_checkpoints"] == []
    ctx = build_tracking_context(result, "TRACK1", None, "DHL")
    assert "Recent history" not in ctx


@pytest.mark.asyncio
async def test_checkpoint_history_keeps_last_three_and_renders_timeline():
    checkpoints = [
        {"checkpoint_time": "2026-01-01T00:00:00Z", "message": "Picked up", "tag": "InfoReceived"},
        {"checkpoint_time": "2026-01-02T00:00:00Z", "message": "In transit", "tag": "InTransit"},
        {"checkpoint_time": "2026-01-03T00:00:00Z", "message": "Arrived at facility", "tag": "InTransit"},
        {"checkpoint_time": "2026-01-04T00:00:00Z", "message": "Out for delivery", "tag": "OutForDelivery"},
    ]
    resp = _FakeResponse(200, json_data={"data": {"tracking": {
        "tag": "OutForDelivery", "checkpoints": checkpoints, "slug": "dhl",
    }}})
    with _patch_client(response=resp):
        result = await get_tracking_status("TRACK1", "dhl", "key")
    assert len(result["recent_checkpoints"]) == 3
    assert result["recent_checkpoints"][0]["message"] == "In transit"  # oldest of the 4 dropped
    ctx = build_tracking_context(result, "TRACK1", None, "DHL")
    assert "Recent history:" in ctx
    assert "→" in ctx


# ─── 2b. Resilience: short-TTL cache, failure cooldown, retry, stats ─────────

_OK_RESPONSE = _FakeResponse(200, json_data={"data": {"tracking": {
    "tag": "InTransit", "checkpoints": [], "slug": "dhl",
}}})


@pytest.mark.asyncio
async def test_second_call_within_ttl_is_served_from_cache_not_network():
    factory = _counting_client_factory(response=_OK_RESPONSE)
    with patch("src.services.tracking_service.httpx.AsyncClient", side_effect=factory):
        first = await get_tracking_status("TRACK1", "dhl", "key")
        second = await get_tracking_status("TRACK1", "dhl", "key")
    assert first == second
    assert factory.calls["n"] == 1  # second call served from cache, no new HTTP attempt
    assert get_tracking_stats()["cache_hit"] == 1


@pytest.mark.asyncio
async def test_expired_cache_entry_triggers_a_fresh_network_call():
    factory = _counting_client_factory(response=_OK_RESPONSE)
    with patch("src.services.tracking_service.httpx.AsyncClient", side_effect=factory):
        await get_tracking_status("TRACK1", "dhl", "key")
        # Force the cached entry into the past instead of sleeping in a test.
        key = ("dhl", "TRACK1")
        expires_at, cached_value = tracking_mod._cache[key]
        tracking_mod._cache[key] = (time.monotonic() - 1, cached_value)
        await get_tracking_status("TRACK1", "dhl", "key")
    assert factory.calls["n"] == 2  # cache miss on the second call — real request made


@pytest.mark.asyncio
async def test_different_tracking_numbers_do_not_share_a_cache_entry():
    factory = _counting_client_factory(response=_OK_RESPONSE)
    with patch("src.services.tracking_service.httpx.AsyncClient", side_effect=factory):
        await get_tracking_status("TRACK1", "dhl", "key")
        await get_tracking_status("TRACK2", "dhl", "key")
    assert factory.calls["n"] == 2


@pytest.mark.asyncio
async def test_retry_recovers_from_a_single_transient_timeout():
    factory = _sequenced_client_factory(
        (None, httpx.TimeoutException("blip")),
        (_OK_RESPONSE, None),
    )
    with patch("src.services.tracking_service.httpx.AsyncClient", side_effect=factory):
        result = await get_tracking_status("TRACK1", "dhl", "key")
    assert result is not None
    assert result["status"] == "InTransit"
    assert factory.calls["n"] == 2  # one failed attempt + one retry
    assert get_tracking_stats()["success"] == 1


@pytest.mark.asyncio
async def test_permanent_404_is_not_retried():
    factory = _counting_client_factory(response=_FakeResponse(404))
    with patch("src.services.tracking_service.httpx.AsyncClient", side_effect=factory):
        result = await get_tracking_status("TRACK1", "dhl", "key")
    assert result is None
    assert factory.calls["n"] == 1  # no retry for a permanent "not found"
    assert get_tracking_stats()["not_found"] == 1


@pytest.mark.asyncio
async def test_persistent_5xx_exhausts_the_one_retry_then_fails():
    factory = _counting_client_factory(response=_FakeResponse(500))
    with patch("src.services.tracking_service.httpx.AsyncClient", side_effect=factory):
        result = await get_tracking_status("TRACK1", "dhl", "key")
    assert result is None
    assert factory.calls["n"] == 2  # original attempt + the one retry, both failed
    assert get_tracking_stats()["http_error"] == 1


@pytest.mark.asyncio
async def test_cooldown_engages_after_threshold_consecutive_failures_and_skips_network():
    factory = _counting_client_factory(response=_FakeResponse(500))
    with patch("src.services.tracking_service.httpx.AsyncClient", side_effect=factory):
        for i in range(tracking_mod._FAILURE_THRESHOLD):
            # Use a distinct tracking number each time so the cache never masks this.
            await get_tracking_status(f"TRACK{i}", "dhl", "key")
        calls_before_cooldown = factory.calls["n"]

        result = await get_tracking_status("TRACK-COOLDOWN", "dhl", "key")

    assert result is None
    assert factory.calls["n"] == calls_before_cooldown  # cooldown skipped the network call entirely
    assert get_tracking_stats()["skipped_cooldown"] == 1


@pytest.mark.asyncio
async def test_cooldown_expires_and_live_calls_resume():
    factory = _counting_client_factory(response=_OK_RESPONSE)
    tracking_mod._consecutive_failures = tracking_mod._FAILURE_THRESHOLD
    tracking_mod._cooldown_until = time.monotonic() - 1  # already expired

    with patch("src.services.tracking_service.httpx.AsyncClient", side_effect=factory):
        result = await get_tracking_status("TRACK1", "dhl", "key")

    assert result is not None
    assert factory.calls["n"] == 1


@pytest.mark.asyncio
async def test_a_successful_404_answer_resets_the_failure_counter():
    """A 404 means Aftership itself is responsive and healthy — it shouldn't
    count toward the cooldown threshold the way a timeout/5xx does."""
    factory = _counting_client_factory(response=_FakeResponse(404))
    with patch("src.services.tracking_service.httpx.AsyncClient", side_effect=factory):
        for i in range(tracking_mod._FAILURE_THRESHOLD + 2):
            await get_tracking_status(f"TRACK{i}", "dhl", "key")
    assert tracking_mod._consecutive_failures == 0


@pytest.mark.asyncio
async def test_stats_counters_reflect_call_outcomes():
    with _patch_client(response=_OK_RESPONSE):
        await get_tracking_status("TRACK1", "dhl", "key")
    stats = get_tracking_stats()
    assert stats["requests"] == 1
    assert stats["success"] == 1


# ─── 3. Webhook secret resolution (dual env-var support) ────────────────────

def _reload_aftership_module():
    import src.api.routes.webhooks.aftership as mod
    return importlib.reload(mod)


def test_secret_resolves_from_AFTERSHIP_WEBHOOK_SECRET_env(monkeypatch):
    monkeypatch.setenv("AFTERSHIP_WEBHOOK_SECRET", "primary-secret")
    monkeypatch.delenv("AFTERSHIP_SECRET", raising=False)
    mod = _reload_aftership_module()
    try:
        assert mod.AFTERSHIP_WEBHOOK_SECRET == "primary-secret"
    finally:
        _reload_aftership_module()


def test_secret_falls_back_to_AFTERSHIP_SECRET_env(monkeypatch):
    monkeypatch.delenv("AFTERSHIP_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("AFTERSHIP_SECRET", "fallback-secret")
    mod = _reload_aftership_module()
    try:
        assert mod.AFTERSHIP_WEBHOOK_SECRET == "fallback-secret"
    finally:
        _reload_aftership_module()


def test_missing_both_secret_env_vars_logs_once_and_disables_verification(monkeypatch, caplog):
    monkeypatch.delenv("AFTERSHIP_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("AFTERSHIP_SECRET", raising=False)
    with caplog.at_level("WARNING"):
        mod = _reload_aftership_module()
        warnings_at_load = len(caplog.records)
        assert mod.AFTERSHIP_WEBHOOK_SECRET is None
        assert any("accepted unverified" in r.message for r in caplog.records)

        # Calling verify repeatedly must not re-log the "not configured" warning per request.
        assert mod.verify_aftership_webhook(b"data", "some-signature") is True
        assert mod.verify_aftership_webhook(b"data", "some-signature") is True
        assert len(caplog.records) == warnings_at_load
    _reload_aftership_module()


# ─── 4. Signature verification ───────────────────────────────────────────────

def test_valid_signature_is_accepted(monkeypatch):
    monkeypatch.setenv("AFTERSHIP_WEBHOOK_SECRET", "test-secret")
    monkeypatch.delenv("AFTERSHIP_SECRET", raising=False)
    mod = _reload_aftership_module()
    try:
        body = b'{"event":"tracking_update"}'
        sig = base64.b64encode(hmac.new(b"test-secret", body, hashlib.sha256).digest()).decode()
        assert mod.verify_aftership_webhook(body, sig) is True
    finally:
        _reload_aftership_module()


def test_invalid_signature_is_rejected(monkeypatch):
    monkeypatch.setenv("AFTERSHIP_WEBHOOK_SECRET", "test-secret")
    monkeypatch.delenv("AFTERSHIP_SECRET", raising=False)
    mod = _reload_aftership_module()
    try:
        body = b'{"event":"tracking_update"}'
        assert mod.verify_aftership_webhook(body, "totally-wrong-signature") is False
    finally:
        _reload_aftership_module()


@pytest.mark.asyncio
async def test_malformed_webhook_payload_returns_400(monkeypatch):
    monkeypatch.delenv("AFTERSHIP_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("AFTERSHIP_SECRET", raising=False)
    mod = _reload_aftership_module()
    try:
        class _FakeRequest:
            def __init__(self, body):
                self._body = body
                self.headers = {}

            async def body(self):
                return self._body

        req = _FakeRequest(b"not-json")
        with pytest.raises(HTTPException) as exc_info:
            await mod.aftership_webhook(req, BackgroundTasks())
        assert exc_info.value.status_code == 400
    finally:
        _reload_aftership_module()


# ─── 5. Idempotency (content-hash fix, replacing the PID fallback) ──────────

@pytest.mark.asyncio
async def test_missing_tracking_number_is_a_noop():
    mod = _reload_aftership_module()
    try:
        with patch.object(mod, "supabase_select") as sel, \
             patch.object(mod, "supabase_insert") as ins:
            await mod.process_aftership_event("tracking_update", {"tag": "InTransit"})
        sel.assert_not_called()
        ins.assert_not_called()
    finally:
        _reload_aftership_module()


@pytest.mark.asyncio
async def test_replayed_webhook_without_stable_id_deduped_via_content_hash():
    mod = _reload_aftership_module()
    try:
        msg = {
            "tracking_number": "TRACK123",
            "tag": "InTransit",
            "checkpoints": [{"checkpoint_time": "2026-01-01T00:00:00Z", "message": "in transit"}],
        }
        existing_store = {}

        def fake_select(table, params=None):
            if table == "webhook_events":
                key = params["event_id"].replace("eq.", "")
                return [{"event_id": key}] if key in existing_store else []
            return []

        def fake_insert(table, data):
            if table == "webhook_events":
                existing_store[data["event_id"]] = data
            return [data]

        def fake_update(table, match, data):
            return [data]

        with patch.object(mod, "supabase_select", side_effect=fake_select), \
             patch.object(mod, "supabase_insert", side_effect=fake_insert), \
             patch.object(mod, "supabase_update", side_effect=fake_update):
            await mod.process_aftership_event("tracking_update", msg)
            assert len(existing_store) == 1

            # Exact replay of the same event (e.g. AfterShip's at-least-once redelivery).
            await mod.process_aftership_event("tracking_update", msg)
            assert len(existing_store) == 1  # still one — deduped, not double-counted
    finally:
        _reload_aftership_module()


@pytest.mark.asyncio
async def test_duplicate_webhook_delivery_with_stable_id_deduped():
    mod = _reload_aftership_module()
    try:
        msg = {"tracking_number": "T1", "tag": "InTransit", "id": "aftership-evt-1", "checkpoints": []}
        existing_store = {}

        def fake_select(table, params=None):
            if table == "webhook_events":
                key = params["event_id"].replace("eq.", "")
                return [{"event_id": key}] if key in existing_store else []
            return []

        def fake_insert(table, data):
            existing_store[data["event_id"]] = data
            return [data]

        def fake_update(table, match, data):
            return [data]

        with patch.object(mod, "supabase_select", side_effect=fake_select), \
             patch.object(mod, "supabase_insert", side_effect=fake_insert), \
             patch.object(mod, "supabase_update", side_effect=fake_update):
            await mod.process_aftership_event("tracking_update", msg)
            await mod.process_aftership_event("tracking_update", msg)
        assert len(existing_store) == 1
    finally:
        _reload_aftership_module()


@pytest.mark.asyncio
async def test_duplicate_tracking_number_across_brands_updates_both_documents_known_gap():
    """Documents the known, un-fixed brand-scoping gap (see plan §4): a webhook
    scoped only by tracking_number will update every order that shares it,
    even across different brands. Not asserting this is correct — asserting
    it's the current, understood behavior."""
    mod = _reload_aftership_module()
    try:
        msg = {"tracking_number": "SHARED1", "tag": "Delivered", "id": "evt-shared-1", "checkpoints": []}
        updated_matches = []

        def fake_select(table, params=None):
            if table == "webhook_events":
                return []
            if table == "orders":
                return [{"id": "order-brandA"}, {"id": "order-brandB"}]
            return []

        def fake_insert(table, data):
            return [data]

        def fake_update(table, match, data):
            updated_matches.append(match)
            return [data]

        with patch.object(mod, "supabase_select", side_effect=fake_select), \
             patch.object(mod, "supabase_insert", side_effect=fake_insert), \
             patch.object(mod, "supabase_update", side_effect=fake_update):
            await mod.process_aftership_event("tracking_update", msg)

        assert len(updated_matches) == 2
    finally:
        _reload_aftership_module()


# ─── 6. Consolidation regression guard ───────────────────────────────────────

def test_customer_success_agent_no_longer_calls_legacy_shipping_status():
    """Structural guard for the Phase 4 consolidation: the legacy V3Tools path
    must not be invoked internally anymore (single AfterShip call per message,
    via tracking_service only). The method itself still exists in tools.py,
    deprecated but not deleted — only the internal call site is gone."""
    from src.agent import customer_success_agent as csa
    source = inspect.getsource(csa)
    assert "get_shipping_status" not in source
    assert '"shipping_status"' not in source
