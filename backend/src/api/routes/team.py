"""
Team Members
============
Invite-based RBAC on top of the existing `tenants` (1 tenant = 1 Shopify
brand owner) model. See migrations/063_team_members.sql and
auth_service.py's "Team membership (RBAC)" section for the resolution
logic shared with TenantContext (tenant_auth.py) and UserContext
(supabase_auth_service.py) — this file only exposes it over HTTP.

Roles are stored internally using the same strings supabase_auth_service.
UserRole already uses elsewhere ("admin" | "agent" | "read_only"), so the
existing require_agent_or_admin/is_admin checks on ticket & action routes
apply to team members with zero changes there. "read_only" is presented to
API clients as "viewer" per product naming.
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr

from src.services.auth_service import auth_service
from src.services.supabase_auth_service import supabase_auth_service
from src.api.middleware.tenant_auth import get_current_tenant, require_tenant_admin, TenantContext
from src.lib.rate_limiter import limiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/team", tags=["Team"])
security = HTTPBearer(auto_error=False)

_API_TO_INTERNAL_ROLE = {"admin": "admin", "agent": "agent", "viewer": "read_only"}
_INTERNAL_TO_API_ROLE = {"admin": "admin", "agent": "agent", "read_only": "viewer"}


def _serialize_member(m: dict) -> dict:
    return {
        "id": m.get("id"),
        "email": m.get("email"),
        "full_name": m.get("full_name"),
        "role": _INTERNAL_TO_API_ROLE.get(m.get("role"), m.get("role")),
        "status": m.get("status"),
        "invited_at": m.get("created_at"),
        "accepted_at": m.get("accepted_at"),
    }


class InviteRequest(BaseModel):
    email: EmailStr
    role: str  # "admin" | "agent" | "viewer"


class AcceptInviteRequest(BaseModel):
    token: str


@router.get("/members")
async def list_members(tenant: TenantContext = Depends(get_current_tenant)):
    """Any authenticated team member can see the roster; only admins get invite/revoke controls (frontend-hidden, backend-enforced below)."""
    members = await auth_service.list_team_members(tenant.tenant_id)
    return {"success": True, "members": [_serialize_member(m) for m in members]}


@router.post("/invite")
@limiter.limit("20/minute")
async def invite_member(request: Request, payload: InviteRequest, tenant: TenantContext = Depends(require_tenant_admin)):
    role = _API_TO_INTERNAL_ROLE.get(payload.role.strip().lower())
    if not role:
        raise HTTPException(status_code=400, detail="role must be one of: admin, agent, viewer")

    if payload.email.strip().lower() == tenant.email.strip().lower():
        raise HTTPException(status_code=400, detail="You are already the account owner")

    result = await auth_service.invite_team_member(tenant.tenant_id, tenant.tenant_id, payload.email, role)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return {"success": True, "member": _serialize_member(result["member"])}


@router.post("/members/{member_id}/revoke")
async def revoke_member(member_id: str, tenant: TenantContext = Depends(require_tenant_admin)):
    # Tenant isolation: revoke_team_member only matches rows scoped to
    # tenant.tenant_id, so a member_id from another tenant 404s below.
    if member_id == tenant.member_id:
        raise HTTPException(status_code=400, detail="You cannot revoke your own access")

    result = await auth_service.revoke_team_member(tenant.tenant_id, member_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return {"success": True}


@router.get("/invites/{token}")
async def preview_invite(token: str):
    """Public — the accept-invite page uses this before the user is signed in."""
    invite = await auth_service.get_invite_by_token(token)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")

    tenant_row = await auth_service.get_tenant(invite["tenant_id"])
    return {
        "success": True,
        "email": invite["email"],
        "role": _INTERNAL_TO_API_ROLE.get(invite["role"], invite["role"]),
        "status": invite["status"],
        "company_name": (tenant_row or {}).get("company_name") or "tResolv",
    }


async def _verified_supabase_identity(credentials: Optional[HTTPAuthorizationCredentials]) -> tuple[str, str]:
    """Verifies the bearer token only — deliberately does NOT resolve/create
    a tenant (unlike get_current_tenant), since a brand-new invitee has none
    yet and must not have an owner tenant spun up for them here."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = supabase_auth_service.verify_jwt(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    supabase_user_id = payload.get("sub")
    email = payload.get("email")
    if not supabase_user_id or not email:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    return supabase_user_id, email


@router.post("/invites/{token}/accept")
async def accept_invite(token: str, credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Called after the invited user has registered/logged in via the
    existing /api/v1/auth flow and holds a Supabase access token."""
    supabase_user_id, email = await _verified_supabase_identity(credentials)

    result = await auth_service.accept_team_invite(token, supabase_user_id, email)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return {"success": True, "tenant_id": result["tenant_id"], "role": _INTERNAL_TO_API_ROLE.get(result["role"], result["role"])}
