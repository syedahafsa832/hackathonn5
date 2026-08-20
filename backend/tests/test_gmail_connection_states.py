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
from src.services.brand_gmail_service import BrandGmailService  # noqa: E402
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

def test_brand_status_connected_reports_needs_reconnect_false():
    brand = {"id": BRAND_ID, "tenant_id": TENANT_ID, "gmail_connected": True, "gmail_email": "merchant@example.com"}
    client = TestClient(_brand_app())
    with patch("src.api.routes.brand_gmail.supabase_select", return_value=[brand]):
        resp = client.get(f"/brands/{BRAND_ID}/gmail/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"connected": True, "email": "merchant@example.com", "needs_reconnect": False}


def test_brand_status_never_connected_reports_needs_reconnect_false():
    brand = {"id": BRAND_ID, "tenant_id": TENANT_ID, "gmail_connected": False, "gmail_email": None}
    client = TestClient(_brand_app())
    with patch("src.api.routes.brand_gmail.supabase_select", return_value=[brand]):
        resp = client.get(f"/brands/{BRAND_ID}/gmail/status")
    body = resp.json()
    assert body == {"connected": False, "email": None, "needs_reconnect": False}


def test_brand_status_revoked_token_reports_needs_reconnect_true():
    """The exact state left behind by brand_gmail_service._build_service()
    after an invalid_grant refresh failure: gmail_connected=False,
    gmail_email still populated."""
    brand = {"id": BRAND_ID, "tenant_id": TENANT_ID, "gmail_connected": False, "gmail_email": "merchant@example.com"}
    client = TestClient(_brand_app())
    with patch("src.api.routes.brand_gmail.supabase_select", return_value=[brand]):
        resp = client.get(f"/brands/{BRAND_ID}/gmail/status")
    body = resp.json()
    assert body == {"connected": False, "email": "merchant@example.com", "needs_reconnect": True}


# ═══════════════════════════════════════════════════════════════════════════
# 1b. saas_settings.py's tenant-scoped /gmail/status
# ═══════════════════════════════════════════════════════════════════════════

def test_settings_status_connected_via_primary_lookup():
    connected_brand = {"id": BRAND_ID, "tenant_id": TENANT_ID, "gmail_connected": True,
                        "gmail_email": "merchant@example.com", "updated_at": "2026-01-01T00:00:00Z"}

    def fake_select(table, params=None):
        if table == "brands" and params.get("gmail_connected") == "is.true":
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


def test_settings_status_revoked_token_falls_back_to_stale_email_and_needs_reconnect():
    """Regression: before this fix, a revoked token made this endpoint lose
    the email entirely and report plain "not connected" - indistinguishable
    from a merchant who never connected Gmail at all."""
    stale_brand = {"id": BRAND_ID, "tenant_id": TENANT_ID, "gmail_connected": False,
                    "gmail_email": "merchant@example.com", "updated_at": "2026-01-01T00:00:00Z"}

    def fake_select(table, params=None):
        # Neither the primary (gmail_connected=true) lookup nor the
        # shopify_domain fallback finds anything, since gmail_connected is
        # now False - only the new stale-email fallback query
        # (gmail_email: not.is.null) should surface this brand.
        if table == "brands" and params.get("gmail_connected") == "is.true":
            return []
        if table == "brands" and "gmail_email" in params:
            return [stale_brand]
        return []

    client = TestClient(_settings_app())
    with patch("src.api.routes.saas_settings.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.saas_settings.auth_service.get_tenant", new=AsyncMock(return_value=None)):
        resp = client.get("/api/v1/settings/gmail/status")
    body = resp.json()
    assert body["connected"] is False
    assert body["email"] == "merchant@example.com"
    assert body["needs_reconnect"] is True


def test_settings_status_manual_disconnect_does_not_look_like_needs_reconnect():
    """disconnect() clears gmail_email too - the stale-email fallback query
    must find nothing, correctly landing on the plain not-connected state."""
    client = TestClient(_settings_app())

    def fake_select(table, params=None):
        return []  # nothing anywhere - both gmail_connected and gmail_email are cleared

    with patch("src.api.routes.saas_settings.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.saas_settings.auth_service.get_tenant", new=AsyncMock(return_value=None)):
        resp = client.get("/api/v1/settings/gmail/status")
    body = resp.json()
    assert body["connected"] is False
    assert body["needs_reconnect"] is False


# ═══════════════════════════════════════════════════════════════════════════
# 2. handle_callback never leaks a raw exception string as the error code
# ═══════════════════════════════════════════════════════════════════════════

def test_handle_callback_generic_failure_returns_stable_code_not_raw_exception():
    service = BrandGmailService()
    with patch("src.services.brand_gmail_service._verify_state", return_value=BRAND_ID), \
         patch("src.services.brand_gmail_service.Flow") as mock_flow_cls:
        mock_flow_cls.from_client_config.side_effect = RuntimeError(
            "invalid_client: Unauthorized (client secret rotated in prod db)"
        )
        result = run(service.handle_callback(state="signed-state", code="auth-code"))

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
        result = run(service.handle_callback(state="signed-state", code="auth-code"))

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
