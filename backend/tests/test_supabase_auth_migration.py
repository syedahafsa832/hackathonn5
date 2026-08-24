"""
Supabase Auth Migration Tests
==============================
Covers the tenant-resolution logic added when authentication moved to
Supabase Auth (auth.users) as the identity source of truth, while `tenants`
stays the merchant/company account and brand-isolation root.

All Supabase REST calls (supabase_select/insert/update) and all GoTrue
calls (supabase_gotrue.*) are mocked — no live Supabase project required.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.auth_service import AuthService, FoundingCohortFullError  # noqa: E402
from src.services.supabase_gotrue import GoTrueError  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


# ─── 1. New Supabase user with no existing tenant → creates one ────────────

def test_new_supabase_user_creates_a_tenant():
    inserted = {}

    def fake_insert(table, data):
        if table == "tenants":
            inserted["tenant"] = {"id": "tenant-new", **data}
            return inserted["tenant"]
        return {"id": "brand-1", **data}

    with patch("src.services.auth_service.supabase_select", return_value=[]), \
         patch("src.services.auth_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.auth_service.supabase_update", return_value={}):
        auth_service = AuthService()
        tenant = _run(auth_service.resolve_or_create_tenant_for_supabase_user(
            "sb-user-1", "new@example.com", "New Co"
        ))

    assert tenant["id"] == "tenant-new"
    assert tenant["supabase_user_id"] == "sb-user-1"
    # Never store a password for a Supabase-Auth-created tenant.
    assert tenant["password_hash"] is None


# ─── 2. Existing supabase_user_id match → returns that tenant, no insert ───

def test_existing_supabase_user_id_maps_straight_to_its_tenant():
    existing = {"id": "tenant-1", "email": "a@b.com", "supabase_user_id": "sb-user-1", "is_active": True}

    def fake_select(table, params=None):
        params = params or {}
        if table == "tenants" and params.get("supabase_user_id") == "eq.sb-user-1":
            return [existing]
        return []

    with patch("src.services.auth_service.supabase_select", side_effect=fake_select), \
         patch("src.services.auth_service.supabase_insert") as mock_insert:
        auth_service = AuthService()
        tenant = _run(auth_service.resolve_or_create_tenant_for_supabase_user("sb-user-1", "a@b.com"))

    assert tenant is existing
    mock_insert.assert_not_called()


# ─── 3. Repeated login with the same Supabase user never creates a second tenant ───

def test_duplicate_login_does_not_create_a_duplicate_tenant():
    store = {}

    def fake_select(table, params=None):
        params = params or {}
        if table != "tenants":
            return []
        rows = list(store.values())
        if "supabase_user_id" in params:
            wanted = params["supabase_user_id"][3:]
            rows = [r for r in rows if r.get("supabase_user_id") == wanted]
        if "email" in params:
            wanted = params["email"][3:]
            rows = [r for r in rows if r.get("email") == wanted]
        if "founding_cohort" in params:
            rows = [r for r in rows if r.get("founding_cohort")]
        return rows

    def fake_insert(table, data):
        if table == "tenants":
            row = {"id": "tenant-1", **data}
            store["tenant-1"] = row
            return row
        return {"id": "brand-1", **data}

    with patch("src.services.auth_service.supabase_select", side_effect=fake_select), \
         patch("src.services.auth_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.auth_service.supabase_update", return_value={}):
        auth_service = AuthService()
        first = _run(auth_service.resolve_or_create_tenant_for_supabase_user("sb-user-1", "dup@example.com"))
        second = _run(auth_service.resolve_or_create_tenant_for_supabase_user("sb-user-1", "dup@example.com"))

    assert first["id"] == second["id"]
    assert len(store) == 1


# ─── 4. Pre-migration tenant (no supabase_user_id yet) links on first Supabase login ───

def test_pre_migration_tenant_links_by_email_on_first_supabase_login():
    """A tenant created before this migration has no supabase_user_id yet.
    Whichever provider (Google or password) they first authenticate with
    through Supabase Auth, they must land on their existing tenant/brands —
    not get a second, empty one — and get backfilled so the next login
    resolves by supabase_user_id directly. (If they later use the *other*
    provider with the same email, Supabase's own identity linking hands back
    this same supabase_user_id, so it resolves straight away — see
    test_existing_supabase_user_id_maps_straight_to_its_tenant.)"""
    existing = {"id": "tenant-1", "email": "both@example.com", "supabase_user_id": None, "is_active": True}
    updated = {}

    def fake_select(table, params=None):
        params = params or {}
        if table != "tenants":
            return []
        if params.get("supabase_user_id") == "eq.sb-id-1":
            return []  # not linked yet — this is the first Supabase login
        if params.get("email") == "eq.both@example.com":
            return [existing]
        return []

    def fake_update(table, match, data):
        updated.update(data)
        return {**existing, **data}

    with patch("src.services.auth_service.supabase_select", side_effect=fake_select), \
         patch("src.services.auth_service.supabase_insert") as mock_insert, \
         patch("src.services.auth_service.supabase_update", side_effect=fake_update):
        auth_service = AuthService()
        tenant = _run(auth_service.resolve_or_create_tenant_for_supabase_user("sb-id-1", "both@example.com"))

    assert tenant["id"] == "tenant-1"
    mock_insert.assert_not_called()
    assert updated["supabase_user_id"] == "sb-id-1"


# ─── 5. Founding cohort cap is enforced for brand-new tenants ──────────────

def test_founding_cohort_cap_blocks_new_tenant_creation():
    def fake_select(table, params=None):
        params = params or {}
        if table == "tenants" and params.get("founding_cohort") == "eq.true":
            return [{"id": f"t{i}"} for i in range(20)]
        return []

    with patch("src.services.auth_service.supabase_select", side_effect=fake_select), \
         patch("src.services.auth_service.supabase_insert") as mock_insert:
        auth_service = AuthService()
        with pytest.raises(FoundingCohortFullError):
            _run(auth_service.resolve_or_create_tenant_for_supabase_user("sb-new", "new@example.com"))

    mock_insert.assert_not_called()


# ─── 6. register() surfaces "check your email" instead of a session when Supabase requires confirmation ───

def test_register_reports_email_confirmation_required_without_a_session():
    def fake_signup(email, password):
        return {"user": {"id": "sb-unconfirmed", "email": email}, "session": None}

    def fake_insert(table, data):
        if table == "tenants":
            return {"id": "tenant-1", **data}
        return {"id": "brand-1", **data}

    with patch("src.services.auth_service.supabase_gotrue.sign_up", side_effect=fake_signup), \
         patch("src.services.auth_service.supabase_select", return_value=[]), \
         patch("src.services.auth_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.auth_service.supabase_update", return_value={}):
        auth_service = AuthService()
        result = _run(auth_service.register("new@example.com", "supersecret123"))

    assert result["success"] is True
    assert result.get("email_confirmation_required") is True
    assert result.get("access_token") is None


# ─── 7. login() never leaks account-existence detail for a bad password ────

def test_login_with_wrong_password_returns_generic_error():
    def fake_signin(email, password):
        raise GoTrueError("Invalid login credentials", 400)

    with patch("src.services.auth_service.supabase_gotrue.sign_in_with_password", side_effect=fake_signin):
        auth_service = AuthService()
        result = _run(auth_service.login("a@b.com", "wrongpassword"))

    assert result["success"] is False
    assert result["error"] == "Invalid email or password"


# ─── 8. change_password re-verifies the current password via a real Supabase sign-in ───

def test_change_password_rejects_when_current_password_is_wrong():
    def fake_signin(email, password):
        raise GoTrueError("Invalid login credentials", 400)

    with patch("src.services.auth_service.supabase_gotrue.sign_in_with_password", side_effect=fake_signin), \
         patch("src.services.auth_service.supabase_gotrue.update_user_password") as mock_update:
        auth_service = AuthService()
        result = _run(auth_service.change_password(
            tenant_id="tenant-1", email="a@b.com",
            current_password="wrong", new_password="newpassword123",
            access_token="at-1",
        ))

    assert result["success"] is False
    assert "incorrect" in result["error"].lower()
    mock_update.assert_not_called()


def test_change_password_succeeds_when_current_password_verifies():
    with patch("src.services.auth_service.supabase_gotrue.sign_in_with_password", return_value={"user": {}}), \
         patch("src.services.auth_service.supabase_gotrue.update_user_password", return_value={}) as mock_update:
        auth_service = AuthService()
        result = _run(auth_service.change_password(
            tenant_id="tenant-1", email="a@b.com",
            current_password="correct", new_password="newpassword123",
            access_token="at-1",
        ))

    assert result["success"] is True
    mock_update.assert_called_once_with("at-1", "newpassword123")


# ─── 9. Password reset never reveals whether the email is registered ───────

def test_password_reset_request_always_reports_success():
    with patch("src.services.auth_service.supabase_gotrue.recover_password", return_value=None) as mock_recover:
        auth_service = AuthService()
        result = _run(auth_service.request_password_reset("unknown@example.com"))

    assert result["success"] is True
    mock_recover.assert_called_once_with("unknown@example.com")
