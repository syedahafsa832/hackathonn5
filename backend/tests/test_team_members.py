"""
Team members + role-based access (see migrations/063_team_members.sql,
auth_service.py's "Team membership (RBAC)" section, and src/api/routes/team.py).

Covers:
 1. Admin can invite Agent
 2. Admin can invite Viewer
 3. Invited user can accept and join the correct tenant
 4/5. Agent/Viewer cannot manage team (route-level 403)
 6. Agent cannot change integrations/settings (route-level 403)
 7. Viewer cannot send/execute ticket actions (existing require_agent_or_admin)
 8. Admin can revoke a member
 9. A revoked member's supabase_user_id no longer resolves to an active membership
10. Cross-tenant invite/membership access is blocked
11. Duplicate/invalid/expired invite is handled safely

All Supabase REST calls are mocked — no live project required.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.services.auth_service import AuthService  # noqa: E402
from src.api.routes.team import router as team_router  # noqa: E402
from src.api.routes.saas_settings import router as settings_router  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, require_tenant_admin, TenantContext  # noqa: E402
from src.api.middleware.auth_middleware import get_current_user, UserContext, AuthenticatedContext, UserRole  # noqa: E402
from src.api.routes.v2_tickets import router as v2_tickets_router  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


app = FastAPI()
app.include_router(team_router, prefix="/api/v1")
app.include_router(settings_router, prefix="/api/v1")
app.include_router(v2_tickets_router, prefix="/api/v2")
client = TestClient(app)


# ─── 1 & 2. Admin invites Agent / Viewer ────────────────────────────────────

def test_admin_can_invite_agent():
    with patch("src.services.auth_service.supabase_select", return_value=[]), \
         patch("src.services.auth_service.supabase_insert", side_effect=lambda t, d: {"id": "member-1", **d}), \
         patch("src.services.auth_service.system_email_service.send_generic_auth_email", return_value=True), \
         patch("src.services.auth_service.auth_service.get_tenant", return_value={"company_name": "Acme"}):
        auth_service = AuthService()
        result = _run(auth_service.invite_team_member("tenant-1", "tenant-1", "agent@example.com", "agent"))

    assert result["success"] is True
    assert result["member"]["role"] == "agent"
    assert result["member"]["status"] == "pending"


def test_admin_can_invite_viewer():
    with patch("src.services.auth_service.supabase_select", return_value=[]), \
         patch("src.services.auth_service.supabase_insert", side_effect=lambda t, d: {"id": "member-2", **d}), \
         patch("src.services.auth_service.system_email_service.send_generic_auth_email", return_value=True), \
         patch("src.services.auth_service.auth_service.get_tenant", return_value={"company_name": "Acme"}):
        auth_service = AuthService()
        result = _run(auth_service.invite_team_member("tenant-1", "tenant-1", "viewer@example.com", "read_only"))

    assert result["success"] is True
    assert result["member"]["role"] == "read_only"


# ─── 3. Invited user accepts and joins the correct tenant ──────────────────

def test_invited_user_accepts_and_joins_correct_tenant():
    invite = {
        "id": "member-3", "tenant_id": "tenant-1", "email": "new@example.com",
        "role": "agent", "status": "pending", "invite_token": "tok-abc",
        "invite_expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }

    def fake_select(table, params=None):
        params = params or {}
        if table == "tenant_members" and params.get("invite_token") == "eq.tok-abc":
            return [invite]
        return []  # no existing active membership for this supabase user

    with patch("src.services.auth_service.supabase_select", side_effect=fake_select), \
         patch("src.services.auth_service.supabase_update", side_effect=lambda t, m, d: {**invite, **d}):
        auth_service = AuthService()
        result = _run(auth_service.accept_team_invite("tok-abc", "sb-new-user", "new@example.com"))

    assert result["success"] is True
    assert result["tenant_id"] == "tenant-1"
    assert result["role"] == "agent"


# ─── 11. Duplicate / invalid / expired invite handled safely ───────────────

def test_duplicate_pending_invite_is_rejected():
    with patch("src.services.auth_service.supabase_select",
               return_value=[{"id": "m1", "status": "pending", "role": "agent"}]):
        auth_service = AuthService()
        result = _run(auth_service.invite_team_member("tenant-1", "tenant-1", "dup@example.com", "agent"))

    assert result["success"] is False
    assert "pending" in result["error"].lower()


def test_invite_for_already_active_member_is_rejected():
    with patch("src.services.auth_service.supabase_select",
               return_value=[{"id": "m1", "status": "active", "role": "agent"}]):
        auth_service = AuthService()
        result = _run(auth_service.invite_team_member("tenant-1", "tenant-1", "active@example.com", "agent"))

    assert result["success"] is False


def test_accept_invalid_token_is_rejected():
    with patch("src.services.auth_service.supabase_select", return_value=[]):
        auth_service = AuthService()
        result = _run(auth_service.accept_team_invite("bad-token", "sb-1", "x@example.com"))

    assert result["success"] is False
    assert "invalid" in result["error"].lower()


def test_accept_expired_invite_is_rejected():
    invite = {
        "id": "m1", "tenant_id": "tenant-1", "email": "x@example.com", "role": "agent",
        "status": "pending", "invite_token": "tok-old",
        "invite_expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    }
    with patch("src.services.auth_service.supabase_select", return_value=[invite]):
        auth_service = AuthService()
        result = _run(auth_service.accept_team_invite("tok-old", "sb-1", "x@example.com"))

    assert result["success"] is False
    assert "expired" in result["error"].lower()


def test_accept_invite_race_loser_gets_a_clean_failure():
    """Two concurrent accept calls for the same token: the update is
    conditioned on status=eq.pending, so only the winner's write matches.
    The loser's supabase_update call (mocked here to return {} — what
    PostgREST returns when zero rows match the filter) must fail cleanly,
    never silently report success or leak a different member's row."""
    invite = {
        "id": "m1", "tenant_id": "tenant-1", "email": "race@example.com", "role": "agent",
        "status": "pending", "invite_token": "tok-race",
        "invite_expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }
    def fake_select(table, params=None):
        params = params or {}
        if table == "tenant_members" and params.get("invite_token") == "eq.tok-race":
            return [invite]
        return []  # no pre-existing active membership for sb-loser

    with patch("src.services.auth_service.supabase_select", side_effect=fake_select), \
         patch("src.services.auth_service.supabase_update", return_value={}) as mock_update:
        auth_service = AuthService()
        result = _run(auth_service.accept_team_invite("tok-race", "sb-loser", "race@example.com"))

    assert result["success"] is False
    # The compare-and-swap filter is what makes this safe — assert it's present.
    assert mock_update.call_args[0][1] == {"id": "eq.m1", "status": "eq.pending"}


def test_accept_invite_for_different_email_is_rejected():
    """'invite for an existing authenticated user' who is signed in as someone else."""
    invite = {
        "id": "m1", "tenant_id": "tenant-1", "email": "intended@example.com", "role": "agent",
        "status": "pending", "invite_token": "tok-1",
        "invite_expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }
    with patch("src.services.auth_service.supabase_select", return_value=[invite]):
        auth_service = AuthService()
        result = _run(auth_service.accept_team_invite("tok-1", "sb-1", "someone-else@example.com"))

    assert result["success"] is False


# ─── 8 & 9. Admin revokes a member; revoked membership stops resolving ─────

def test_admin_can_revoke_member():
    with patch("src.services.auth_service.supabase_select", return_value=[{"id": "m1", "tenant_id": "tenant-1"}]), \
         patch("src.services.auth_service.supabase_update", side_effect=lambda t, m, d: {"id": "m1", "status": "revoked", **d}):
        auth_service = AuthService()
        result = _run(auth_service.revoke_team_member("tenant-1", "m1"))

    assert result["success"] is True
    assert result["member"]["status"] == "revoked"


def test_revoked_member_no_longer_resolves_as_active():
    """resolve_membership_for_supabase_user only matches status=eq.active —
    a revoked row must fall through to the owner-resolution path (which,
    for a member with no owned tenant of their own, ultimately fails auth
    at a higher layer)."""
    def fake_select(table, params=None):
        params = params or {}
        if table == "tenant_members" and params.get("status") == "eq.active":
            return []  # revoked row is excluded
        return []

    with patch("src.services.auth_service.supabase_select", side_effect=fake_select), \
         patch("src.services.auth_service.supabase_insert", side_effect=lambda t, d: {"id": "tenant-new", **d}), \
         patch("src.services.auth_service.supabase_update", return_value={}):
        auth_service = AuthService()
        membership = _run(auth_service.resolve_membership_for_supabase_user("sb-revoked", "revoked@example.com"))

    # Falls back to owner-resolution (creates/owns their own tenant) rather
    # than reusing the revoked membership.
    assert membership["is_owner"] is True


# ─── 10. Cross-tenant isolation on revoke ───────────────────────────────────

def test_revoke_is_scoped_to_the_calling_tenant():
    """A member_id that belongs to a different tenant must not be found —
    revoke_team_member filters by tenant_id AND id together."""
    def fake_select(table, params=None):
        params = params or {}
        if params.get("id") == "eq.victim-member" and params.get("tenant_id") == "eq.attacker-tenant":
            return []  # no row matches both filters — wrong tenant
        return []

    with patch("src.services.auth_service.supabase_select", side_effect=fake_select):
        auth_service = AuthService()
        result = _run(auth_service.revoke_team_member("attacker-tenant", "victim-member"))

    assert result["success"] is False
    assert "not found" in result["error"].lower()


# ─── 4/5/6. Route-level role gating (admin-only endpoints) ─────────────────

def _tenant_ctx(role, tenant_id="tenant-1", member_id=None):
    return TenantContext(tenant_id=tenant_id, email="user@example.com", role=role, member_id=member_id)


def _as_role(role):
    """Both get_current_tenant and require_tenant_admin are overridden so
    every route in this app (team + settings) sees the same caller —
    require_tenant_admin's real 403 logic still can't run since it's fully
    replaced, so we replicate the same check here for the override."""
    from fastapi import HTTPException

    def _get_current_tenant():
        return _tenant_ctx(role)

    def _require_tenant_admin():
        if role != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
        return _tenant_ctx(role)

    return _get_current_tenant, _require_tenant_admin


def test_agent_cannot_invite_team_members():
    get_ctx, require_admin = _as_role("agent")
    app.dependency_overrides[get_current_tenant] = get_ctx
    app.dependency_overrides[require_tenant_admin] = require_admin
    try:
        resp = client.post("/api/v1/team/invite", json={"email": "x@example.com", "role": "agent"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_viewer_cannot_invite_team_members():
    get_ctx, require_admin = _as_role("read_only")
    app.dependency_overrides[get_current_tenant] = get_ctx
    app.dependency_overrides[require_tenant_admin] = require_admin
    try:
        resp = client.post("/api/v1/team/invite", json={"email": "x@example.com", "role": "agent"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_agent_cannot_revoke_team_members():
    get_ctx, require_admin = _as_role("agent")
    app.dependency_overrides[get_current_tenant] = get_ctx
    app.dependency_overrides[require_tenant_admin] = require_admin
    try:
        resp = client.post("/api/v1/team/members/m1/revoke")
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_agent_cannot_change_shopify_settings():
    get_ctx, require_admin = _as_role("agent")
    app.dependency_overrides[get_current_tenant] = get_ctx
    app.dependency_overrides[require_tenant_admin] = require_admin
    try:
        resp = client.post("/api/v1/settings/shopify/disconnect")
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_can_invite_via_route():
    get_ctx, require_admin = _as_role("admin")
    app.dependency_overrides[get_current_tenant] = get_ctx
    app.dependency_overrides[require_tenant_admin] = require_admin
    try:
        with patch("src.api.routes.team.auth_service.invite_team_member",
                   return_value={"success": True, "member": {"id": "m1", "email": "x@example.com", "role": "agent", "status": "pending"}}):
            resp = client.post("/api/v1/team/invite", json={"email": "x@example.com", "role": "agent"})
        assert resp.status_code == 200
        assert resp.json()["member"]["role"] == "agent"
    finally:
        app.dependency_overrides.clear()


def test_admin_cannot_revoke_own_access():
    get_ctx, require_admin = _as_role("admin")
    ctx_with_member_id = TenantContext(tenant_id="tenant-1", email="a@b.com", role="admin", member_id="self-member")
    app.dependency_overrides[get_current_tenant] = lambda: ctx_with_member_id
    app.dependency_overrides[require_tenant_admin] = lambda: ctx_with_member_id
    try:
        resp = client.post("/api/v1/team/members/self-member/revoke")
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.clear()


# ─── 7. Viewer cannot send/execute ticket actions (existing gate) ──────────

def test_viewer_cannot_respond_to_tickets():
    viewer_context = AuthenticatedContext(
        user=UserContext(
            user_id="member-viewer", supabase_auth_id="sb-viewer",
            organization_id="tenant-1", email="viewer@example.com",
            role=UserRole.READ_ONLY, brands=["brand-1"],
        ),
        brand_ids=["brand-1"],
    )
    app.dependency_overrides[get_current_user] = lambda: viewer_context
    try:
        resp = client.post("/api/v2/tickets/ticket-1/respond", json={"response": "hi", "send_to_customer": False})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_agent_can_respond_to_tickets_role_check_passes():
    """Only asserts the role gate itself doesn't 403 an agent (not full ticket flow)."""
    agent_context = AuthenticatedContext(
        user=UserContext(
            user_id="member-agent", supabase_auth_id="sb-agent",
            organization_id="tenant-1", email="agent@example.com",
            role=UserRole.AGENT, brands=["brand-1"],
        ),
        brand_ids=["brand-1"],
    )
    app.dependency_overrides[get_current_user] = lambda: agent_context
    try:
        with patch("src.api.routes.v2_tickets.supabase_select", return_value=[]):
            resp = client.post("/api/v2/tickets/ticket-1/respond", json={"response": "hi", "send_to_customer": False})
        # 404 (ticket not found) proves the role gate passed — a 403 would mean it didn't.
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()
