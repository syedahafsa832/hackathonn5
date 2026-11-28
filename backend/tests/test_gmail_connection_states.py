"""
Gmail connection state/error-handling fixes for the onboarding UX pass.

Covers three backend changes:

1. Both Gmail status endpoints (brand_gmail.py's per-brand
   /brands/{brand_id}/gmail/status and saas_settings.py's tenant-scoped
   /settings/gmail/status) now return a `needs_reconnect` flag, computed
   from data that already existed: brand_gmail_service._build_service()
   flips gmail_connected to False on a revoked/expired refresh token
   (invalid_grant) but deliberately leaves gmail_email in place - the only
   signal that distinguishes "never connected" from "was connected, now
   needs reconnecting". A manual disconnect() clears both fields, so that
   case still reports needs_reconnect=False, same as true first-time state.

2. brand_gmail_service.handle_callback()'s generic exception handler used to
   return str(e) as the "error" value, which the /gmail/callback route puts
   directly into a redirect URL query string - leaking internal exception
   text and producing whatever a raw googleapiclient error message happens
   to be (spaces, colons, unpredictable content) instead of a stable code
   the frontend can map to real copy. It now returns one of a small set of
   stable codes.

3. /brands/gmail/callback correctly threads every distinguishable failure
   (Google's own `error` param, missing code/state, and handle_callback's
   own error code) through to the frontend redirect unchanged.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import brand_gmail  # noqa: E402
from src.api.routes import saas_settings  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402
from src.services.brand_gmail_service import BrandGmailService, _sign_state  # noqa: E402
import asyncio  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


TENANT_ID = "tenant-1"
BRAND_ID = "brand-1"


def _brand_app():
    app = FastAPI()
    app.include_router(brand_gmail.router)

    def fake_tenant():
        return TenantContext(tenant_id=TENANT_ID, email="merchant@example.com")

    app.dependency_overrides[get_current_tenant] = fake_tenant
    return app


def _settings_app():
    app = FastAPI()
    app.include_router(saas_settings.router, prefix="/api/v1")

    def fake_tenant():
        return TenantContext(tenant_id=TENANT_ID, email="merchant@example.com")

    app.dependency_overrides[get_current_tenant] = fake_tenant
    return app


# ═══════════════════════════════════════════════════════════════════════════
# 1a. brand_gmail.py's per-brand /gmail/status
# ═══════════════════════════════════════════════════════════════════════════

def test_brand_status_reports_send_only_connection():
    brand = {
        "id": BRAND_ID, "tenant_id": TENANT_ID,
        "gmail_connected": False, "gmail_email": None,
        "gmail_send_connected": True, "gmail_send_email": "merchant@example.com",
    }
    client = TestClient(_brand_app())
    with patch("src.api.routes.brand_gmail.supabase_select", return_value=[brand]):
        resp = client.get(f"/brands/{BRAND_ID}/gmail/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "connected": True, "email": "merchant@example.com",
        "needs_reconnect": False, "send_connected": True, "send_email": "merchant@example.com",
    }

def test_brand_status_never_connected_reports_needs_reconnect_false():
    brand = {
        "id": BRAND_ID, "tenant_id": TENANT_ID,
        "gmail_connected": True, "gmail_email": "legacy@example.com",
        "gmail_send_connected": False, "gmail_send_email": None,
    }
    client = TestClient(_brand_app())
    with patch("src.api.routes.brand_gmail.supabase_select", return_value=[brand]):
        resp = client.get(f"/brands/{BRAND_ID}/gmail/status")
    body = resp.json()
    assert body == {
        "connected": False, "email": None,
        "needs_reconnect": False, "send_connected": False, "send_email": None,
    }

def test_brand_status_revoked_send_only_token_reports_needs_reconnect_true():
    brand = {
        "id": BRAND_ID, "tenant_id": TENANT_ID,
        "gmail_connected": False, "gmail_send_connected": False,
        "gmail_send_email": "merchant@example.com",
    }
    client = TestClient(_brand_app())
    with patch("src.api.routes.brand_gmail.supabase_select", return_value=[brand]):
        resp = client.get(f"/brands/{BRAND_ID}/gmail/status")
    body = resp.json()
    assert body["connected"] is False
    assert body["email"] == "merchant@example.com"
    assert body["needs_reconnect"] is True

def test_settings_status_connected_via_primary_lookup():
    connected_brand = {
        "id": BRAND_ID, "tenant_id": TENANT_ID, "is_active": True,
        "gmail_send_connected": True, "gmail_send_email": "merchant@example.com",
        "updated_at": "2026-01-01T00:00:00Z",
    }

    def fake_select(table, params=None):
        if table == "brands" and params.get("tenant_id") == f"eq.{TENANT_ID}":
            return [connected_brand]
        return []

    client = TestClient(_settings_app())
    with patch("src.api.routes.saas_settings.supabase_select", side_effect=fake_select):
        resp = client.get("/api/v1/settings/gmail/status")
    body = resp.json()
    assert body["connected"] is True
    assert body["email"] == "merchant@example.com"
    assert body["needs_reconnect"] is False

def test_settings_status_never_connected_reports_needs_reconnect_false():
    client = TestClient(_settings_app())
    with patch("src.api.routes.saas_settings.supabase_select", return_value=[]), \
         patch("src.api.routes.saas_settings.auth_service.get_tenant", new=AsyncMock(return_value=None)):
        resp = client.get("/api/v1/settings/gmail/status")
    assert resp.json() == {"connected": False, "email": None, "needs_reconnect": False}

def test_settings_status_reports_stale_send_only_email_for_reconnect():
    stale_brand = {
        "id": BRAND_ID, "tenant_id": TENANT_ID, "is_active": True,
        "gmail_send_connected": False, "gmail_send_email": "merchant@example.com",
    }
    client = TestClient(_settings_app())
    with patch("src.api.routes.saas_settings.supabase_select", return_value=[stale_brand]):
        resp = client.get("/api/v1/settings/gmail/status")
    body = resp.json()
    assert body["connected"] is False
    assert body["email"] == "merchant@example.com"
    assert body["needs_reconnect"] is True

def test_settings_status_manual_disconnect_does_not_look_like_needs_reconnect():
    client = TestClient(_settings_app())
    with patch("src.api.routes.saas_settings.supabase_select", return_value=[]), \
         patch("src.api.routes.saas_settings.auth_service.get_tenant", new=AsyncMock(return_value=None)):
        resp = client.get("/api/v1/settings/gmail/status")
    body = resp.json()
    assert body["connected"] is False
    assert body["needs_reconnect"] is False

def test_email_forwarding_consent_records_version_and_timestamp():
    brand = {
        "id": BRAND_ID, "tenant_id": TENANT_ID,
        "inbound_email_token": "a" * 40,
    }
    client = TestClient(_brand_app())
    with patch("src.api.routes.brand_gmail.supabase_select", return_value=[brand]), \
         patch("src.api.routes.brand_gmail.supabase_update", return_value={}) as update:
        response = client.post(f"/brands/{BRAND_ID}/email-forwarding/consent")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["consent_version"] == "2026-11-28-v1"
    assert body["consent_recorded_at"]
    changes = update.call_args.args[2]
    assert changes["email_ai_processing_consent_version"] == "2026-11-28-v1"
    assert changes["email_ai_processing_consent_at"] == body["consent_recorded_at"]
    # Address must be enabled for Google verification email capture, but it
    # remains in gmail_polling mode until explicit forwarding activation.
    assert changes["inbound_email_enabled"] is True


def test_email_forwarding_activation_requires_current_consent():
    brand = {
        "id": BRAND_ID, "tenant_id": TENANT_ID,
        "inbound_email_token": "a" * 40,
        "inbound_email_verified_at": "2026-11-28T10:00:00+00:00",
        "gmail_send_connected": True,
        "gmail_send_token": "encrypted-send-token",
        "email_ai_processing_consent_at": None,
        "email_ai_processing_consent_version": None,
    }
    client = TestClient(_brand_app())
    with patch("src.api.routes.brand_gmail.supabase_select", return_value=[brand]):
        response = client.post(f"/brands/{BRAND_ID}/email-forwarding/activate")

    assert response.status_code == 409
    assert "consent" in response.json()["detail"].lower()


def test_email_forwarding_activation_enables_only_after_current_consent():
    brand = {
        "id": BRAND_ID, "tenant_id": TENANT_ID,
        "inbound_email_token": "a" * 40,
        "inbound_email_verified_at": "2026-11-28T10:00:00+00:00",
        "gmail_send_connected": True,
        "gmail_send_token": "encrypted-send-token",
        "email_ai_processing_consent_at": "2026-11-28T11:00:00+00:00",
        "email_ai_processing_consent_version": "2026-11-28-v1",
        "inbound_email_enabled": False,
    }
    client = TestClient(_brand_app())
    with patch("src.api.routes.brand_gmail.supabase_select", return_value=[brand]), \
         patch("src.api.routes.brand_gmail.supabase_update", return_value={}) as update:
        response = client.post(f"/brands/{BRAND_ID}/email-forwarding/activate")

    assert response.status_code == 200
    changes = update.call_args.args[2]
    assert changes["inbound_email_enabled"] is True
    assert changes["email_ingestion_mode"] == "forwarding"


def test_legacy_gmail_auth_url_returns_gone():
    client = TestClient(_brand_app())
    brand = {"id": BRAND_ID, "tenant_id": TENANT_ID, "is_active": True}
    with patch("src.api.routes.brand_gmail.supabase_select", return_value=[brand]):
        response = client.get(f"/brands/{BRAND_ID}/gmail/auth-url")
    assert response.status_code == 410


def test_handle_callback_generic_failure_returns_stable_code_not_raw_exception():
    service = BrandGmailService()
    with patch("src.services.brand_gmail_service._verify_state", return_value=BRAND_ID), \
         patch("src.services.brand_gmail_service.Flow") as mock_flow_cls:
        mock_flow_cls.from_client_config.side_effect = RuntimeError(
            "invalid_client: Unauthorized (client secret rotated in prod db)"
        )
        result = run(service.handle_callback(state=_sign_state(BRAND_ID, scope_mode="send_only"), code="auth-code"))

    assert result["success"] is False
    # The real exception text (which could contain internal detail) must
    # never be the value that ends up in a public redirect URL.
    assert result["error"] == "connection_failed"
    assert "client secret" not in result["error"]
    assert "Unauthorized" not in result["error"]


def test_handle_callback_scope_related_failure_returns_scope_mismatch_code():
    service = BrandGmailService()
    with patch("src.services.brand_gmail_service._verify_state", return_value=BRAND_ID), \
         patch("src.services.brand_gmail_service.Flow") as mock_flow_cls:
        mock_flow_cls.from_client_config.side_effect = RuntimeError("Scope has changed from requested scope")
        result = run(service.handle_callback(state=_sign_state(BRAND_ID, scope_mode="send_only"), code="auth-code"))

    assert result == {"success": False, "error": "scope_mismatch"}


def test_handle_callback_invalid_state_still_reported_directly():
    """Unchanged behavior - this code path was already a stable enum, not a
    raw exception, and must keep being reported as-is."""
    service = BrandGmailService()
    with patch("src.services.brand_gmail_service._verify_state", return_value=None):
        result = run(service.handle_callback(state="tampered", code="auth-code"))
    assert result == {"success": False, "error": "invalid_or_expired_state"}


# ═══════════════════════════════════════════════════════════════════════════
# 3. /brands/gmail/callback route threads every error case through cleanly
# ═══════════════════════════════════════════════════════════════════════════

def test_callback_route_passes_through_google_denial():
    client = TestClient(_brand_app())
    resp = client.get("/brands/gmail/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "gmail_error=access_denied" in resp.headers["location"]


def test_callback_route_reports_missing_params():
    client = TestClient(_brand_app())
    resp = client.get("/brands/gmail/callback", follow_redirects=False)  # no code, no state
    assert "gmail_error=missing_params" in resp.headers["location"]


def test_callback_route_success_redirect_includes_email():
    client = TestClient(_brand_app())
    with patch("src.api.routes.brand_gmail.brand_gmail_service.handle_callback",
               new=AsyncMock(return_value={"success": True, "email": "merchant@example.com"})):
        resp = client.get("/brands/gmail/callback", params={"code": "auth-code", "state": "signed-state"}, follow_redirects=False)
    location = resp.headers["location"]
    assert "gmail_connected=1" in location
    assert "email=merchant%40example.com" in location or "email=merchant@example.com" in location


def test_callback_route_failure_redirect_uses_stable_error_code():
    client = TestClient(_brand_app())
    with patch("src.api.routes.brand_gmail.brand_gmail_service.handle_callback",
               new=AsyncMock(return_value={"success": False, "error": "connection_failed"})):
        resp = client.get("/brands/gmail/callback", params={"code": "auth-code", "state": "signed-state"}, follow_redirects=False)
    assert "gmail_error=connection_failed" in resp.headers["location"]
