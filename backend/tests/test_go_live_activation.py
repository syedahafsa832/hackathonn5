"""
Safe go-live activation: new merchants must not go live automatically.

Design (see auth_service.py register() and admin.py update_ai_mode/get_ai_mode):
- New brands get an explicit system_settings row with ai_mode="paused" created
  at signup, in the SAME request that creates the brand row.
- Existing brands are untouched: get_system_settings() still falls back to
  ai_mode="active" when no row exists, exactly as before this change - so a
  brand that predates this feature and has no settings row keeps working
  exactly as it did.
- Gmail polling (brand_gmail_service.get_connected_brands()) is unchanged -
  connecting Gmail alone never writes to system_settings.
- The existing admin.py /ai-mode PATCH endpoint (already tenant-scoped via
  _assert_brand_access) is reused, unmodified, as the "Go Live" write path -
  no second activation system was built.
- ai_mode="paused" already routes through message_processor.py's existing
  STAGE 8 logic (_decide_ticket_routing) as draft-only, never auto-sent -
  see test_paused_mode_stores_draft_for_review_never_auto_sends in
  test_escalation_routing.py for that half of the guarantee.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.auth_service import AuthService  # noqa: E402
from src.services.supabase_service import SupabaseService  # noqa: E402
from src.services.brand_gmail_service import BrandGmailService  # noqa: E402
from src.api.middleware.tenant_auth import TenantContext  # noqa: E402


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _FakeRequest:
    """Minimal stand-in for starlette Request - only .body()/.json() are used
    by update_ai_mode."""
    method = "PATCH"

    def __init__(self, payload: bytes):
        self._payload = payload

    async def body(self):
        return self._payload

    async def json(self):
        import json
        return json.loads(self._payload)


def _mock_insert_recorder():
    """Returns (fake_insert, inserted_calls) - fake_insert records every
    (table, data) call and returns a plausible row for tenants/brands so
    register() can keep flowing (it reads .get('id') off both)."""
    inserted = []

    def fake_insert(table, data):
        inserted.append((table, dict(data)))
        if table == "tenants":
            return {"id": "tenant-1", **data}
        if table == "brands":
            return {"id": "brand-1", **data}
        return {"id": "row-1", **data}

    return fake_insert, inserted


# ── 1. New brand defaults to paused ─────────────────────────────────────────

def test_new_brand_registration_creates_paused_system_settings_row():
    service = AuthService()
    fake_insert, inserted = _mock_insert_recorder()
    fake_signup = {
        "user": {"id": "sb-user-1", "email": "new@example.com"},
        "session": {"access_token": "at-1", "refresh_token": "rt-1", "token_type": "bearer", "expires_in": 3600},
    }
    with patch("src.services.auth_service.supabase_select", return_value=[]), \
         patch("src.services.auth_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.auth_service.supabase_gotrue.sign_up", return_value=fake_signup):
        result = run(service.register(email="new@example.com", password="password123", company_name="Test Co"))

    assert result["success"] is True
    settings_calls = [data for table, data in inserted if table == "system_settings"]
    assert len(settings_calls) == 1
    assert settings_calls[0]["store_id"] == "brand-1"
    assert settings_calls[0]["ai_mode"] == "paused"


# ── 2. Existing active brand remains active ─────────────────────────────────

def test_existing_brand_with_explicit_active_row_stays_active():
    """A brand that already has a system_settings row (created before this
    feature, or explicitly activated) is read as-is - registration logic is
    never re-run for it, and get_system_settings() just returns its row."""
    service = SupabaseService()
    with patch("src.services.supabase_service.supabase_select", return_value=[{"store_id": "old-brand", "ai_mode": "active"}]):
        settings = run(service.get_system_settings("old-brand"))
    assert settings["ai_mode"] == "active"


def test_existing_brand_with_no_settings_row_still_defaults_active():
    """CRITICAL: brands created before this feature have no system_settings
    row at all. The global fallback in get_system_settings() must still be
    'active' - changing it would silently pause every existing live merchant.
    This test locks that fallback in place."""
    service = SupabaseService()
    with patch("src.services.supabase_service.supabase_select", return_value=[]):
        settings = run(service.get_system_settings("some-pre-existing-brand-with-no-row"))
    assert settings["ai_mode"] == "active"


# ── 3. Gmail connection alone does not activate AI ──────────────────────────

def test_connecting_gmail_never_touches_system_settings():
    """get_connected_brands() (what the poller uses) only ever reads the
    brands table - it must never read or write system_settings, so simply
    having gmail_connected=true can't be what flips ai_mode."""
    service = BrandGmailService()
    with patch("src.services.brand_gmail_service.supabase_select", return_value=[]) as mock_select:
        service.get_connected_brands()
    tables_queried = [call.args[0] for call in mock_select.call_args_list]
    assert "system_settings" not in tables_queried
    assert tables_queried == ["brands"]


# ── 4. Paused brand is not processed as live ────────────────────────────────

def test_new_brand_default_paused_mode_never_auto_sends():
    """A brand still in its default post-signup paused state must have replies
    stored as a draft, never auto-sent to a real customer - reusing the
    existing STAGE 8 routing logic unchanged."""
    from src.workers.message_processor import UnifiedMessageProcessor
    processor = UnifiedMessageProcessor()
    result = processor._decide_ticket_routing(
        ai_mode="paused", is_overridden=False, confidence=0.95,
        confidence_threshold=0.65, ai_flagged_escalate=False,
        risk_level="low", reply_body="This would have been sent",
    )
    assert result["should_auto_reply"] is False
    assert result["status"] == "ai_suggested"


# ── 5. Explicit Go Live changes the existing mode to active ─────────────────

def test_go_live_patch_sets_ai_mode_active_for_the_specific_brand():
    """The onboarding 'Go Live' button calls the existing admin.py /ai-mode
    PATCH endpoint with the merchant's own brand as store_id - reused
    unmodified, no second activation system."""
    from src.api.routes import admin as admin_module

    tenant = TenantContext(tenant_id="tenant-1", email="owner@example.com")
    request = _FakeRequest(b'{"mode": "active", "store_id": "brand-1"}')

    update_calls = []

    def fake_update(table, match, data):
        update_calls.append((table, match, data))
        return [{"store_id": "brand-1", **data}]  # non-empty -> row existed, no insert needed

    with patch("src.api.routes.admin._get_tenant_brand_ids", return_value=["brand-1"]), \
         patch("src.api.routes.admin.supabase_update", side_effect=fake_update), \
         patch("src.api.routes.admin.supabase_service") as mock_service:
        mock_service.log_audit = _AsyncNoop()
        result = run(admin_module.update_ai_mode(request=request, tenant=tenant))

    assert result == {"status": "success", "mode": "active"}
    assert len(update_calls) == 1
    assert update_calls[0][1] == {"store_id": "eq.brand-1"}
    assert update_calls[0][2] == {"ai_mode": "active"}


class _AsyncNoop:
    def __call__(self, *args, **kwargs):
        async def _inner():
            return None
        return _inner()


# ── 6. Refresh/reload preserves the actual backend state ────────────────────

def test_ai_mode_reads_are_never_stale_across_calls():
    """The onboarding Go Live step re-fetches status on every mount (page
    refresh included) via get_system_settings() - it must reflect whatever is
    currently persisted, not a cached value from an earlier call."""
    service = SupabaseService()
    with patch("src.services.supabase_service.supabase_select", return_value=[{"store_id": "brand-1", "ai_mode": "paused"}]):
        first = run(service.get_system_settings("brand-1"))
    assert first["ai_mode"] == "paused"

    with patch("src.services.supabase_service.supabase_select", return_value=[{"store_id": "brand-1", "ai_mode": "active"}]):
        second = run(service.get_system_settings("brand-1"))
    assert second["ai_mode"] == "active"


# ── 7. Existing active merchants remain unaffected ──────────────────────────

def test_registration_change_does_not_touch_any_existing_brand_row():
    """The new system_settings insert in register() is scoped to the brand ID
    just created in the SAME call - it can never reference or modify a
    pre-existing brand/settings row."""
    service = AuthService()
    fake_insert, inserted = _mock_insert_recorder()
    fake_signup = {
        "user": {"id": "sb-user-2", "email": "second@example.com"},
        "session": {"access_token": "at-2", "refresh_token": "rt-2", "token_type": "bearer", "expires_in": 3600},
    }
    with patch("src.services.auth_service.supabase_select", return_value=[]), \
         patch("src.services.auth_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.auth_service.supabase_gotrue.sign_up", return_value=fake_signup), \
         patch("src.services.auth_service.supabase_update") as mock_update:
        run(service.register(email="second@example.com", password="password123", company_name="Second Co"))

    # register() never calls supabase_update at all - it only ever inserts
    # new rows (tenants, brands, system_settings), so no existing row anywhere
    # can be modified by a new signup.
    mock_update.assert_not_called()
