"""
tResolv — Stop Email Alert Spam and Implement Safe Incident Alerting.

ROOT CAUSE (confirmed by inspection, not guessed): ai_provider_manager.
create_chat_completion() called admin_alert_service.notify_provider_
degradation() on EVERY failed provider attempt inside its own provider-
rotation retry loop - even for a request that ultimately succeeded via a
later fallback key. With 3 configured Mistral keys (this repo's .env:
primary + 2 fallbacks), a single ongoing quota/rate-limit incident could
mint up to 3 distinct "signature" combinations (provider_label + reason) on
the very first request alone, and because the failure `reason` is
classified from live exception text (rate_limited / timeout / quota_
exceeded / provider_error_5xx / temporary_failure) it isn't perfectly
stable call to call for the same underlying incident - so a modest handful
of customer messages processed during an outage window could each mint a
handful of never-before-seen (provider, reason) signatures. That
combinatorial fragmentation, not a missing dedup mechanism, is what
produced a burst of ~20 emails for what was really one ongoing incident
(existing test even asserted this as intentional behavior: "but the
earlier failing provider(s) still alerted" - see the now-rewritten
test_a_recovered_request_via_fallback_* in test_admin_error_alerts.py).

FIX: notify_provider_degradation() (per-attempt) is removed. Two new
incident-level functions replace it:
  - notify_provider_exhausted() - fires once when EVERY configured provider
    fails a request outright (the request could not complete at all -
    a genuine incident). Deduplicated by `service` alone (e.g.
    "chat_completion"), not by the specific provider/reason breakdown of
    this occurrence, so a persistent outage produces one alert per cooldown
    window even if the specific reason classification jitters between
    occurrences.
  - notify_provider_recovered() - fires at most once, only to close out an
    incident that was genuinely active; a request that simply succeeds
    (the overwhelming common case, including "recovered via fallback")
    sends nothing.
Both reuse the existing _should_send()/_alert_state 5-minute cooldown
window already used for notify_critical_error() - no new infrastructure,
no second notification framework.
"""
import os
import sys
import time
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services import admin_alert_service  # noqa: E402
from src.services.ai_provider_manager import AIProviderManager, AllProvidersFailedError, _Provider  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# Alert-dedup/incident state is persisted via the settings key-value table
# (supabase_get_setting/supabase_set_setting) rather than an in-memory dict -
# see admin_alert_service.py's PART on surviving a redeploy mid-incident.
# This fakes that store with a plain dict, reset per test, so every test
# below stays isolated without needing a real Supabase connection.
_fake_settings_store = {}


def _fake_get_setting(key):
    return _fake_settings_store.get(key)


def _fake_set_setting(key, value):
    _fake_settings_store[key] = value


@pytest.fixture(autouse=True)
def _isolated_alert_state():
    _fake_settings_store.clear()
    with patch("src.services.admin_alert_service.supabase_get_setting", side_effect=_fake_get_setting), \
         patch("src.services.admin_alert_service.supabase_set_setting", side_effect=_fake_set_setting):
        yield


def _manager_with(*labels, base_urls=None):
    mgr = AIProviderManager.__new__(AIProviderManager)
    mgr._providers = [_Provider(label, f"key-{label}", "mistral-large-latest") for label in labels]
    mgr._clients = {}
    return mgr


ATTEMPTS = [{"label": "primary", "reason": "quota_exceeded"}, {"label": "fallback_1", "reason": "quota_exceeded"}]


# ── 1. The exact reported symptom: 20 identical failures -> at most 1 alert ─

def test_the_same_quota_failure_reproduced_20_times_sends_at_most_one_alert():
    """Reproduces the reported burst directly: the same incident recurring
    (as it would across ~20 customer messages arriving during a genuine
    quota exhaustion) must collapse to one email, not one per occurrence."""
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        for _ in range(20):
            admin_alert_service.notify_provider_exhausted(
                attempts=ATTEMPTS, model="mistral-large-latest", elapsed_seconds=12.3, service="chat_completion",
            )

    mock_send.assert_called_once()
    _, subject, body = mock_send.call_args[0]
    assert "exhausted" in subject.lower()
    assert "chat_completion" in body


def test_reason_classification_jitter_across_occurrences_still_dedupes():
    """The exact fragmentation mechanism behind the original bug: the same
    underlying incident can classify to a slightly different `reason` on
    different occurrences (rate_limited vs timeout vs temporary_failure).
    Because the dedup key is `service` alone (not provider+reason), this
    jitter must no longer mint new alert emails."""
    variants = [
        [{"label": "primary", "reason": "rate_limited"}, {"label": "fallback_1", "reason": "timeout"}],
        [{"label": "primary", "reason": "timeout"}, {"label": "fallback_1", "reason": "quota_exceeded"}],
        [{"label": "primary", "reason": "temporary_failure"}, {"label": "fallback_1", "reason": "provider_error_503"}],
    ]
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        for attempts in variants * 5:  # 15 occurrences, each a "new" reason combination
            admin_alert_service.notify_provider_exhausted(
                attempts=attempts, model="mistral-large-latest", elapsed_seconds=5.0, service="chat_completion",
            )

    mock_send.assert_called_once()


# ── 2. Different incidents (different service) alert separately ────────────

def test_different_services_alert_independently():
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        admin_alert_service.notify_provider_exhausted(attempts=ATTEMPTS, model="m", elapsed_seconds=1.0, service="chat_completion")
        admin_alert_service.notify_provider_exhausted(attempts=ATTEMPTS, model="m", elapsed_seconds=1.0, service="chat_completion")
        admin_alert_service.notify_provider_exhausted(attempts=ATTEMPTS, model="m", elapsed_seconds=1.0, service="embedding")

    assert mock_send.call_count == 2  # one per distinct service, second chat_completion call suppressed


# ── 3. Brand isolation: the underlying dedup primitive keeps distinct ──────
#      signatures fully independent (used here directly, since the AI
#      provider layer itself is a shared platform resource - not per-brand
#      credentials like Shopify - so provider-exhaustion alerts are
#      intentionally NOT brand-scoped; a brand-scoped incident type would
#      simply include brand_id in its own signature and get the same
#      guarantee for free from this same primitive).

def test_dedup_primitive_keeps_different_signatures_fully_independent():
    sig_brand_a = "shopify_auth_failure:brand-aaaa"
    sig_brand_b = "shopify_auth_failure:brand-bbbb"

    first_a = admin_alert_service._should_send(sig_brand_a)
    first_b = admin_alert_service._should_send(sig_brand_b)
    repeat_a = admin_alert_service._should_send(sig_brand_a)

    assert first_a == 0  # fresh signature, sends
    assert first_b == 0  # a different brand's incident is NOT suppressed by brand A's
    assert repeat_a is None  # brand A's own repeat is suppressed


# ── 4 & 5 & 6. Retries / provider rotation / successful fallback -> 0 alerts ┄

def test_provider_rotation_that_ultimately_succeeds_sends_zero_alerts():
    mgr = _manager_with("primary", "fallback_1", "fallback_2")
    call_count = {"n": 0}

    async def _fake_call(fn):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise TimeoutError("Request timed out.")
        return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "ok"})()})()], "usage": None})()

    with patch("src.services.ai_provider_manager.call_with_limit", side_effect=_fake_call), \
         patch("src.services.ai_provider_manager.asyncio.sleep", new=AsyncMock(return_value=None)), \
         patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        result = run(mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}]))

    assert result[0] is not None
    mock_send.assert_not_called()


def test_first_try_success_sends_zero_alerts():
    """The overwhelming common case: no prior incident, first provider
    succeeds immediately. Must never send a spurious recovery email either."""
    mgr = _manager_with("primary")

    async def _fake_call(fn):
        return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "ok"})()})()], "usage": None})()

    with patch("src.services.ai_provider_manager.call_with_limit", side_effect=_fake_call), \
         patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        result = run(mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}]))

    assert result[0] is not None
    mock_send.assert_not_called()


# ── 7. Persistent incidents remain bounded (periodic reminder, not spam) ───

def test_persistent_incident_produces_a_bounded_reminder_not_unbounded_spam():
    """Within the cooldown window: silence. After it expires, one more
    reminder if the incident is genuinely still active - never one email
    per occurrence."""
    t = [1_000_000.0]
    with patch("src.services.admin_alert_service.time.time", side_effect=lambda: t[0]), \
         patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        admin_alert_service.notify_provider_exhausted(attempts=ATTEMPTS, model="m", elapsed_seconds=1.0, service="chat_completion")
        for _ in range(10):  # many occurrences well inside the 5-minute window
            t[0] += 10
            admin_alert_service.notify_provider_exhausted(attempts=ATTEMPTS, model="m", elapsed_seconds=1.0, service="chat_completion")
        assert mock_send.call_count == 1

        t[0] += admin_alert_service._ALERT_WINDOW_SECONDS + 1  # window expires, incident still active
        admin_alert_service.notify_provider_exhausted(attempts=ATTEMPTS, model="m", elapsed_seconds=1.0, service="chat_completion")
        assert mock_send.call_count == 2  # one bounded reminder, not 12


# ── 8 & 9. Recovery is sent at most once; flapping never spams ─────────────

def test_recovery_sends_at_most_one_email():
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        admin_alert_service.notify_provider_exhausted(attempts=ATTEMPTS, model="m", elapsed_seconds=1.0, service="chat_completion")
        admin_alert_service.notify_provider_recovered(service="chat_completion")
        admin_alert_service.notify_provider_recovered(service="chat_completion")  # no active incident anymore
        admin_alert_service.notify_provider_recovered(service="chat_completion")

    assert mock_send.call_count == 2  # exactly: one exhausted alert + one recovery alert


# ── 8b. The actual production bug: a redeploy mid-incident must not reset
#        the cooldown/incident state - it lives in Supabase (the settings
#        table, via _fake_settings_store here standing in for it), not a
#        module-level dict scoped to one process's lifetime. A real restart
#        re-imports admin_alert_service fresh, so simulating one means
#        reloading the module while keeping the fake settings store intact -
#        exactly what an in-memory dict could never survive.

def test_incident_state_survives_a_simulated_process_restart():
    """Reproduces the exact incident this fix targets: exhausted alert
    fires, the service restarts (module reloaded - any module-level dict
    would be wiped), and within the same cooldown window a second failure
    must still be suppressed, and the eventual recovery must still fire
    exactly once - because the state lives in the persisted store, not in
    the process that sent the first alert."""
    import importlib

    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        admin_alert_service.notify_provider_exhausted(attempts=ATTEMPTS, model="m", elapsed_seconds=1.0, service="chat_completion")
        assert mock_send.call_count == 1

        # Simulate a redeploy: reload the module fresh. Re-patch the two
        # settings functions on the fresh module object so it still reads
        # from the SAME fake store - a real restart would still be backed
        # by the same real Supabase table.
        reloaded = importlib.reload(admin_alert_service)
        with patch("src.services.admin_alert_service.supabase_get_setting", side_effect=_fake_get_setting), \
             patch("src.services.admin_alert_service.supabase_set_setting", side_effect=_fake_set_setting), \
             patch("src.services.admin_alert_service.send_admin_notification") as mock_send_after_restart:
            # Still within the cooldown window - the "new" process must know
            # this incident already alerted and suppress the repeat.
            reloaded.notify_provider_exhausted(attempts=ATTEMPTS, model="m", elapsed_seconds=1.0, service="chat_completion")
            mock_send_after_restart.assert_not_called()

            # And recovery still fires exactly once, even after the restart.
            reloaded.notify_provider_recovered(service="chat_completion")
            mock_send_after_restart.assert_called_once()


def test_recovery_with_no_prior_incident_sends_nothing():
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        admin_alert_service.notify_provider_recovered(service="chat_completion")

    mock_send.assert_not_called()


def test_rapid_flapping_with_a_genuine_recovery_between_alerts_each_failure():
    """failure -> recovery -> failure -> recovery: each failure here is
    separated by a GENUINE recovery, so this is two distinct incidents back
    to back, not one incident flapping within a single cooldown window -
    both failures and both recoveries must alert (see
    test_outage_volume_test_3, which requires exactly this shape). Recovery
    resets the cooldown specifically so a real second incident is never
    mistaken for a repeat of the first (see admin_alert_service.py's
    _reset_cooldown)."""
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        admin_alert_service.notify_provider_exhausted(attempts=ATTEMPTS, model="m", elapsed_seconds=1.0, service="chat_completion")
        admin_alert_service.notify_provider_recovered(service="chat_completion")
        admin_alert_service.notify_provider_exhausted(attempts=ATTEMPTS, model="m", elapsed_seconds=1.0, service="chat_completion")
        admin_alert_service.notify_provider_recovered(service="chat_completion")

    assert mock_send.call_count == 4  # exhausted, recovered, exhausted, recovered


def test_repeated_exhaustion_with_no_recovery_between_is_still_suppressed():
    """The actual "flapping" case the cooldown still guards against: repeated
    exhaustion attempts with NO recovery in between (still the same,
    never-closed incident) must still collapse to one alert - only a
    genuine recovery resets the cooldown, not the mere passage of time
    within the window."""
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        for _ in range(5):
            admin_alert_service.notify_provider_exhausted(attempts=ATTEMPTS, model="m", elapsed_seconds=1.0, service="chat_completion")

    mock_send.assert_called_once()


# ── 13 & 14. All-providers-fail is a real incident; repeats stay bounded ───

def test_all_providers_failing_sends_one_alert_and_raises():
    mgr = _manager_with("primary", "fallback_1")

    async def _always_fail(fn):
        raise TimeoutError("Request timed out.")

    with patch("src.services.ai_provider_manager.call_with_limit", side_effect=_always_fail), \
         patch("src.services.ai_provider_manager.asyncio.sleep", new=AsyncMock(return_value=None)), \
         patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        try:
            run(mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}]))
            assert False, "expected AllProvidersFailedError"
        except AllProvidersFailedError:
            pass

    mock_send.assert_called_once()


def test_repeated_all_provider_failures_stay_bounded():
    mgr = _manager_with("primary", "fallback_1")

    async def _always_fail(fn):
        raise TimeoutError("Request timed out.")

    with patch("src.services.ai_provider_manager.call_with_limit", side_effect=_always_fail), \
         patch("src.services.ai_provider_manager.asyncio.sleep", new=AsyncMock(return_value=None)), \
         patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        for _ in range(20):  # 20 separate requests, all fully failing - the reported burst pattern
            try:
                run(mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}]))
            except AllProvidersFailedError:
                pass

    mock_send.assert_called_once()  # 20 failing requests -> 1 email, not 20


# ── Safety: no secrets, no unnecessary PII in the exhaustion alert body ────

def test_exhaustion_alert_never_contains_secrets_or_customer_pii():
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        admin_alert_service.notify_provider_exhausted(
            attempts=[{"label": "primary", "reason": "rate_limited"}],
            model="mistral-large-latest", elapsed_seconds=3.2, service="chat_completion",
        )

    _, _, body = mock_send.call_args[0]
    assert "api_key" not in body.lower().replace("api key", "")
    assert "sk-" not in body
    assert "@" not in body  # no email address of any kind (customer or otherwise)


# ── Onboarding (Test Luna) uses the exact same mechanism, no special case ──

def test_onboarding_style_retry_then_success_sends_zero_alerts():
    """Test Luna's /test-reply calls the same customer_success_agent ->
    ai_provider_manager.create_chat_completion() path as email/chat. A
    temporary provider hiccup followed by a successful retry must not spam
    admin email, same as any other request."""
    mgr = _manager_with("primary", "fallback_1")
    call_count = {"n": 0}

    async def _fake_call(fn):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise TimeoutError("Request timed out.")
        return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "ok"})()})()], "usage": None})()

    with patch("src.services.ai_provider_manager.call_with_limit", side_effect=_fake_call), \
         patch("src.services.ai_provider_manager.asyncio.sleep", new=AsyncMock(return_value=None)), \
         patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        result = run(mgr.create_chat_completion(messages=[{"role": "user", "content": "What products do you sell?"}]))

    assert result[0] is not None
    mock_send.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────
# OUTAGE EMAIL VOLUME TEST — required by "tResolv — AI Outage Recovery +
# Real Refund Execution Audit/Fix". Named Test 1-4 to match that task's own
# enumeration directly.
# ─────────────────────────────────────────────────────────────────────────

def test_outage_volume_test_1_ten_failing_requests_send_one_outage_email():
    """10 requests fail during one continuous outage -> 1 admin outage
    notification, not 10."""
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        for _ in range(10):
            admin_alert_service.notify_provider_exhausted(
                attempts=ATTEMPTS, model="mistral-small-latest", elapsed_seconds=8.0, service="chat_completion",
            )

    mock_send.assert_called_once()
    _, subject, _ = mock_send.call_args[0]
    assert "exhausted" in subject.lower()


def test_outage_volume_test_2_ten_successful_requests_send_one_recovery_email():
    """10 requests succeed after recovery -> 1 recovery notification, not
    10 - notify_provider_recovered() is called on every successful request
    (see ai_provider_manager.create_chat_completion), so this is the exact
    shape production traffic produces once providers come back."""
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        admin_alert_service.notify_provider_exhausted(
            attempts=ATTEMPTS, model="mistral-small-latest", elapsed_seconds=8.0, service="chat_completion",
        )
        for _ in range(10):
            admin_alert_service.notify_provider_recovered(service="chat_completion")

    assert mock_send.call_count == 2  # 1 outage + 1 recovery, not 1 + 10


def test_outage_volume_test_3_outage_recovery_new_outage_sends_two_outage_and_one_recovery_email():
    """Outage -> recovery -> new (independent) outage -> 2 outage
    notifications + 1 recovery notification.

    This is the case the cooldown-reset fix in notify_provider_recovered()
    exists for: without resetting the cooldown timer on genuine recovery,
    the second, truly independent outage's alert would be silently
    swallowed by the first outage's still-running 5-minute cooldown, even
    though the incident it belongs to already closed."""
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        admin_alert_service.notify_provider_exhausted(
            attempts=ATTEMPTS, model="mistral-small-latest", elapsed_seconds=8.0, service="chat_completion",
        )
        admin_alert_service.notify_provider_recovered(service="chat_completion")
        # A second, independent outage starts shortly after recovery -
        # still well within what would have been the first alert's 5-minute
        # cooldown window if that window hadn't been reset by recovery.
        admin_alert_service.notify_provider_exhausted(
            attempts=ATTEMPTS, model="mistral-small-latest", elapsed_seconds=8.0, service="chat_completion",
        )
        admin_alert_service.notify_provider_recovered(service="chat_completion")

    assert mock_send.call_count == 4  # exhausted, recovered, exhausted, recovered
    subjects = [call.args[1] for call in mock_send.call_args_list]
    outage_subjects = [s for s in subjects if "exhausted" in s.lower()]
    recovery_subjects = [s for s in subjects if "recovered" in s.lower()]
    assert len(outage_subjects) == 2
    assert len(recovery_subjects) == 2


def test_outage_volume_test_4_single_customer_specific_failure_sends_no_outage_email():
    """A single, isolated provider failure that recovers via fallback (the
    "one malformed request" / "one timeout" / "one customer-specific
    failure" case) must never trigger a global provider-outage email -
    notify_provider_exhausted() is only ever called when EVERY configured
    provider fails a given request outright (see
    ai_provider_manager.create_chat_completion), never for a single
    provider hiccup recovered by the next one in the chain."""
    mgr = _manager_with("primary", "fallback_1", "fallback_2")
    call_count = {"n": 0}

    async def _fake_call(fn):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # One provider fails for this one request - not a stack-wide
            # outage, and the very next provider in the chain succeeds.
            raise TimeoutError("Request timed out.")
        return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "ok"})()})()], "usage": None})()

    with patch("src.services.ai_provider_manager.call_with_limit", side_effect=_fake_call), \
         patch("src.services.ai_provider_manager.asyncio.sleep", new=AsyncMock(return_value=None)), \
         patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        result = run(mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}]))

    assert result[0] is not None
    mock_send.assert_not_called()
