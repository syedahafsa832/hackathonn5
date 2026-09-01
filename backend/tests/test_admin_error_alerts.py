"""
Critical-error admin email alerts.

LoggingMiddleware (src/api/middleware/logging.py) is the one place that
already wraps every request — it was defined but never actually registered
on the app (main.py never called setup_logging_middleware), so this fix also
wires it up there. This is where the admin alert is integrated: an
unhandled exception, or a response/HTTPException carrying a 5xx, triggers
admin_alert_service.notify_critical_error(); a normal HTTPException under
500 (400/401/403/404/etc) must never trigger it.

Dedup/rate-limiting and redaction are unit-tested directly against
admin_alert_service since they're pure, signature-keyed logic that doesn't
need a real request round-trip.
"""
import os
import sys
from unittest.mock import patch, AsyncMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.middleware.logging import setup_logging_middleware  # noqa: E402
from src.services import admin_alert_service  # noqa: E402


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


def _build_app():
    app = FastAPI()
    setup_logging_middleware(app)

    @app.get("/boom-unhandled")
    async def boom_unhandled():
        raise ValueError("something truly unexpected broke")

    @app.get("/boom-500")
    async def boom_500():
        raise HTTPException(status_code=500, detail="Shopify sync failed")

    @app.get("/bad-request")
    async def bad_request():
        raise HTTPException(status_code=400, detail="Missing required field")

    @app.get("/not-found")
    async def not_found():
        raise HTTPException(status_code=404, detail="Ticket not found")

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    return app


# ── 1. Unexpected failures trigger the alert, expected ones don't ──────────

def test_unhandled_exception_sends_admin_email():
    app = _build_app()
    client = TestClient(app, raise_server_exceptions=False)
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        resp = client.get("/boom-unhandled")

    assert resp.status_code == 500
    mock_send.assert_called_once()
    to_email, subject, body = mock_send.call_args[0]
    assert to_email == admin_alert_service.ADMIN_ALERT_EMAIL
    assert "tResolv ERROR" in subject
    assert "ValueError" in body
    assert "something truly unexpected broke" in body


def test_explicit_5xx_http_exception_sends_admin_email():
    """An HTTPException(5xx) raised in a route is already converted into a
    Response by Starlette's ExceptionMiddleware before this middleware ever
    sees it as an exception - so this is caught by the post-call_next
    status-code check, not the except block. The detail text isn't
    available there without unsafely re-reading the response stream, but
    route/method/status still are."""
    app = _build_app()
    client = TestClient(app, raise_server_exceptions=False)
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        resp = client.get("/boom-500")

    assert resp.status_code == 500
    mock_send.assert_called_once()
    _, subject, body = mock_send.call_args[0]
    assert "tResolv ERROR" in subject
    assert "/boom-500" in body
    assert "500" in body


def test_expected_400_does_not_trigger_alert():
    app = _build_app()
    client = TestClient(app, raise_server_exceptions=False)
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        resp = client.get("/bad-request")

    assert resp.status_code == 400
    mock_send.assert_not_called()


def test_expected_404_does_not_trigger_alert():
    app = _build_app()
    client = TestClient(app, raise_server_exceptions=False)
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        resp = client.get("/not-found")

    assert resp.status_code == 404
    mock_send.assert_not_called()


def test_normal_success_does_not_trigger_alert():
    app = _build_app()
    client = TestClient(app, raise_server_exceptions=False)
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        resp = client.get("/ok")

    assert resp.status_code == 200
    mock_send.assert_not_called()


# ── 2. Redaction ─────────────────────────────────────────────────────────

def test_sensitive_values_are_redacted():
    raw = (
        "Traceback (most recent call last):\n"
        "  File 'app.py', line 1\n"
        "password: hunter2\n"
        "Authorization: Bearer sk-live-abc123def456\n"
        "api_key=sk-1234567890\n"
        "refresh_token: rt_verysecretvalue\n"
        "normal message with no secrets"
    )
    redacted = admin_alert_service.redact_text(raw)

    assert "hunter2" not in redacted
    assert "sk-live-abc123def456" not in redacted
    assert "sk-1234567890" not in redacted
    assert "rt_verysecretvalue" not in redacted
    assert "[REDACTED]" in redacted
    assert "normal message with no secrets" in redacted


def test_redaction_applied_before_email_is_sent():
    app = _build_app()

    @app.get("/boom-secret")
    async def boom_secret():
        raise ValueError("auth failed, api_key=sk-super-secret-value-123")

    client = TestClient(app, raise_server_exceptions=False)
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        client.get("/boom-secret")

    _, _, body = mock_send.call_args[0]
    assert "sk-super-secret-value-123" not in body
    assert "[REDACTED]" in body


# ── 3. Dedup / rate limiting ─────────────────────────────────────────────

def test_repeated_identical_errors_are_deduplicated():
    app = _build_app()
    client = TestClient(app, raise_server_exceptions=False)
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        client.get("/boom-unhandled")
        client.get("/boom-unhandled")
        client.get("/boom-unhandled")

    # Only the first occurrence within the window actually sends.
    mock_send.assert_called_once()


def test_alert_resumes_after_window_expires_and_reports_suppressed_count():
    signature = "ValueError:GET:/x"
    # Simulate an alert sent well outside the window, with 23 suppressed since.
    _fake_set_setting(f"{admin_alert_service._SETTING_PREFIX}{signature}", {
        "last_sent": 0.0,
        "suppressed": 23,
        "incident_active": False,
    })
    with patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        admin_alert_service.notify_critical_error(
            error_type="ValueError", error_message="boom again", route="/x", method="GET",
            status_code=500, request_id="req-1",
        )

    mock_send.assert_called_once()
    _, _, body = mock_send.call_args[0]
    assert "23" in body
    assert "suppressed" in body.lower()


# ── 4. Reliability: a failing notification must not break the real response ─

def test_admin_alert_failure_does_not_break_the_original_error_response():
    app = _build_app()
    client = TestClient(app, raise_server_exceptions=False)
    with patch("src.services.admin_alert_service.send_admin_notification", side_effect=RuntimeError("SMTP down")):
        resp = client.get("/boom-unhandled")

    # The customer still gets the normal error response - the notification
    # failure was swallowed, not surfaced.
    assert resp.status_code == 500


# ── 5. Provider exhaustion / recovery incident pair. Individual retries and
#      provider rotation within a single request must NEVER alert on their
#      own - only true exhaustion (every configured provider failed and the
#      request could not complete) and its later recovery do. See
#      test_provider_alert_deduplication.py for the full spam-fix test
#      matrix; these two just confirm the fallback-recovers-cleanly case at
#      the real create_chat_completion() call site.

def test_a_recovered_request_via_fallback_sends_no_alert_at_all():
    """The exact live scenario this closes: primary/fallback_1/fallback_2
    all time out, groq_fallback_1 finally succeeds and the endpoint returns
    200. Previously each failed provider independently alerted even though
    the request ultimately succeeded - that per-attempt alerting is exactly
    what produced a burst of ~20 emails for one ongoing incident. A request
    that recovers via fallback must now send zero admin emails."""
    import asyncio
    import os
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.environ.setdefault("MISTRAL_API_KEY_PRIMARY", "k1")
    from src.services.ai_provider_manager import AIProviderManager

    mgr = AIProviderManager()
    if len(mgr._providers) < 2:
        import pytest
        pytest.skip("needs at least 2 configured providers in this environment")

    call_count = {"n": 0}

    async def _fake_call(fn):
        call_count["n"] += 1
        if call_count["n"] < len(mgr._providers):
            raise TimeoutError("Request timed out.")
        response = type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "ok"})()})()], "usage": None})()
        return response

    with patch("src.services.ai_provider_manager.call_with_limit", side_effect=_fake_call), \
         patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        result = asyncio.get_event_loop().run_until_complete(
            mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}])
        )

    assert result[0] is not None  # ultimately succeeded
    mock_send.assert_not_called()  # zero alerts, even though earlier providers failed


def test_every_provider_failing_sends_exactly_one_exhaustion_alert():
    """The genuine incident case: no configured provider can complete the
    request at all. This must still alert - it's a real operational problem,
    not a retry that recovered."""
    import asyncio
    import os
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.environ.setdefault("MISTRAL_API_KEY_PRIMARY", "k1")
    from src.services.ai_provider_manager import AIProviderManager, AllProvidersFailedError

    mgr = AIProviderManager()
    if len(mgr._providers) < 1:
        import pytest
        pytest.skip("no providers configured in this environment")

    async def _always_fail(fn):
        raise TimeoutError("Request timed out.")

    with patch("src.services.ai_provider_manager.call_with_limit", side_effect=_always_fail), \
         patch("src.services.ai_provider_manager.asyncio.sleep", new=AsyncMock(return_value=None)), \
         patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        try:
            asyncio.get_event_loop().run_until_complete(
                mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}])
            )
            assert False, "expected AllProvidersFailedError"
        except AllProvidersFailedError:
            pass

    mock_send.assert_called_once()
    _, subject, body = mock_send.call_args[0]
    assert "exhausted" in subject.lower()
    assert "chat_completion" in body
