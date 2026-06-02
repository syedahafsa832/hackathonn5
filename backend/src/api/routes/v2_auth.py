"""
Authentication API Routes (v2)
==============================
Supabase Auth integration with organization management.
"""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field, EmailStr

from src.api.middleware.auth_middleware import (
    AuthenticatedContext,
    get_current_user,
    get_optional_auth,
    require_admin
)
from src.services.supabase_auth_service import (
    supabase_auth_service,
    UserRole
)
from src.lib.supabase_client import supabase_select, supabase_update, supabase_rpc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])


# ==================== Request/Response Models ====================

class SignupRequest(BaseModel):
    """
    Signup request - used after Supabase Auth signup succeeds.
    Creates organization and user records.
    """
    supabase_auth_id: str = Field(..., description="ID from Supabase Auth")
    email: EmailStr
    organization_name: str = Field(..., min_length=1, max_length=255)
    full_name: Optional[str] = None


class AcceptInviteRequest(BaseModel):
    """Accept an invitation to join an organization"""
    token: str
    supabase_auth_id: str
    full_name: Optional[str] = None


class InviteUserRequest(BaseModel):
    """Invite a new team member"""
    email: EmailStr
    role: str = Field(default="agent", pattern=r"^(admin|agent|read_only)$")


class UpdateUserRequest(BaseModel):
    """Update user profile"""
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None


class UpdateUserRoleRequest(BaseModel):
    """Update a user's role (admin only)"""
    role: str = Field(..., pattern=r"^(admin|agent|read_only)$")


# ==================== Public Routes ====================

@router.post("/signup/complete")
async def complete_signup(request: SignupRequest):
    """
    Complete signup after Supabase Auth registration.

    This creates the organization and links the user.
    Call this endpoint after `supabase.auth.signUp()` succeeds.
    """
    try:
        # Check if user already exists
        existing = supabase_select("users", {
            "supabase_auth_id": f"eq.{request.supabase_auth_id}"
        })
        if existing:
            return {
                "success": True,
                "message": "User already exists",
                "organization_id": existing[0]["organization_id"],
                "user_id": existing[0]["id"]
            }

        # Create org and user
        result = await supabase_auth_service.signup_with_organization(
            email=request.email,
            organization_name=request.organization_name,
            full_name=request.full_name,
            supabase_auth_id=request.supabase_auth_id
        )

        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))

        logger.info(f"Signup completed: {request.email}")

        return {
            "success": True,
            "message": "Organization created successfully",
            "organization_id": result["organization_id"],
            "user_id": result["user_id"],
            "role": result["role"]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Signup error: {e}")
        raise HTTPException(status_code=500, detail="Signup failed")


@router.post("/invite/accept")
async def accept_invitation(request: AcceptInviteRequest):
    """
    Accept an invitation to join an organization.

    Call this after the invited user signs up with Supabase Auth.
    """
    try:
        result = await supabase_auth_service.accept_invitation(
            token=request.token,
            supabase_auth_id=request.supabase_auth_id,
            full_name=request.full_name
        )

        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))

        logger.info(f"Invitation accepted: {request.token[:8]}...")

        return {
            "success": True,
            "message": "Invitation accepted",
            "organization_id": result["organization_id"],
            "user_id": result["user_id"],
            "role": result["role"]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Accept invite error: {e}")
        raise HTTPException(status_code=500, detail="Failed to accept invitation")


@router.get("/invite/verify")
async def verify_invitation(token: str = Query(...)):
    """Verify an invitation token is valid"""
    try:
        invitations = supabase_select("invitations", {
            "token": f"eq.{token}",
            "accepted_at": "is.null"
        })

        if not invitations:
            return {"valid": False, "error": "Invalid or expired invitation"}

        invitation = invitations[0]

        from datetime import datetime, timezone
        expires_at = datetime.fromisoformat(invitation["expires_at"].replace("Z", "+00:00"))
        if expires_at < datetime.now(timezone.utc):
            return {"valid": False, "error": "Invitation expired"}

        # Get organization name
        orgs = supabase_select("organizations", {
            "id": f"eq.{invitation['organization_id']}"
        })
        org_name = orgs[0]["name"] if orgs else "Unknown"

        return {
            "valid": True,
            "email": invitation["email"],
            "role": invitation["role"],
            "organization_name": org_name
        }

    except Exception as e:
        logger.error(f"Verify invite error: {e}")
        return {"valid": False, "error": "Verification failed"}


# ==================== Protected Routes ====================

@router.get("/me")
async def get_current_user_info(
    context: AuthenticatedContext = Depends(get_current_user)
):
    """Get current user information"""
    try:
        # Get user details
        users = supabase_select("users", {
            "id": f"eq.{context.user.user_id}"
        })

        if not users:
            raise HTTPException(status_code=404, detail="User not found")

        user = users[0]

        # Get organization
        org = None
        if context.organization:
            org = {
                "id": context.organization.id,
                "name": context.organization.name,
                "slug": context.organization.slug,
                "plan": context.organization.plan
            }

        return {
            "user": {
                "id": user["id"],
                "email": user["email"],
                "full_name": user.get("full_name"),
                "avatar_url": user.get("avatar_url"),
                "role": user["role"],
                "created_at": user["created_at"]
            },
            "organization": org,
            "brands": context.brand_ids,
            "permissions": context.user.permissions
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get user error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get user info")


@router.patch("/me")
async def update_current_user(
    request: UpdateUserRequest,
    context: AuthenticatedContext = Depends(get_current_user)
):
    """Update current user profile"""
    try:
        updates = request.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        result = supabase_update(
            "users",
            {"id": f"eq.{context.user.user_id}"},
            updates
        )

        return {
            "success": True,
            "message": "Profile updated",
            "user": result[0] if result else None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update user error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update profile")


# ==================== Team Management (Admin) ====================

@router.get("/team")
async def list_team_members(
    context: AuthenticatedContext = Depends(get_current_user)
):
    """List all team members in the organization"""
    try:
        users = supabase_select("users", {
            "organization_id": f"eq.{context.user.organization_id}",
            "order": "created_at.asc"
        })

        # Get pending invitations
        invitations = supabase_select("invitations", {
            "organization_id": f"eq.{context.user.organization_id}",
            "accepted_at": "is.null"
        })

        return {
            "members": users or [],
            "pending_invitations": invitations or [],
            "count": len(users or [])
        }

    except Exception as e:
        logger.error(f"List team error: {e}")
        raise HTTPException(status_code=500, detail="Failed to list team")


@router.post("/team/invite")
async def invite_team_member(
    request: InviteUserRequest,
    context: AuthenticatedContext = Depends(require_admin)
):
    """Invite a new team member (Admin only)"""
    try:
        # Check plan limits
        if context.organization:
            limits = context.organization.plan_limits
            max_users = limits.get("users", 2)

            current_users = supabase_select("users", {
                "organization_id": f"eq.{context.user.organization_id}",
                "is_active": "eq.true"
            })

            if len(current_users or []) >= max_users:
                raise HTTPException(
                    status_code=403,
                    detail=f"Plan limit reached: {max_users} users allowed"
                )

        # Check if user already exists
        existing = supabase_select("users", {
            "organization_id": f"eq.{context.user.organization_id}",
            "email": f"eq.{request.email}"
        })
        if existing:
            raise HTTPException(status_code=400, detail="User already in organization")

        # Check for pending invitation
        pending = supabase_select("invitations", {
            "organization_id": f"eq.{context.user.organization_id}",
            "email": f"eq.{request.email}",
            "accepted_at": "is.null"
        })
        if pending:
            raise HTTPException(status_code=400, detail="Invitation already pending")

        # Create invitation
        result = await supabase_auth_service.invite_user(
            organization_id=context.user.organization_id,
            email=request.email,
            role=request.role,
            invited_by=context.user.user_id
        )

        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))

        # TODO: Send invitation email
        invite_url = f"/invite?token={result['token']}"

        logger.info(f"Invitation sent: {request.email} by {context.user.email}")

        return {
            "success": True,
            "message": f"Invitation sent to {request.email}",
            "invite_url": invite_url,
            "expires_at": result["expires_at"]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Invite error: {e}")
        raise HTTPException(status_code=500, detail="Failed to send invitation")


@router.patch("/team/{user_id}/role")
async def update_user_role(
    user_id: str,
    request: UpdateUserRoleRequest,
    context: AuthenticatedContext = Depends(require_admin)
):
    """Update a team member's role (Admin only)"""
    try:
        # Verify user is in same org
        users = supabase_select("users", {
            "id": f"eq.{user_id}",
            "organization_id": f"eq.{context.user.organization_id}"
        })

        if not users:
            raise HTTPException(status_code=404, detail="User not found")

        # Prevent demoting yourself from admin if you're the only admin
        if user_id == context.user.user_id and request.role != "admin":
            admins = supabase_select("users", {
                "organization_id": f"eq.{context.user.organization_id}",
                "role": "eq.admin",
                "is_active": "eq.true"
            })
            if len(admins or []) <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot remove last admin"
                )

        result = supabase_update(
            "users",
            {"id": f"eq.{user_id}"},
            {"role": request.role}
        )

        logger.info(f"Role updated: {user_id} to {request.role} by {context.user.email}")

        return {
            "success": True,
            "message": "Role updated",
            "user": result[0] if result else None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update role error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update role")


@router.delete("/team/{user_id}")
async def remove_team_member(
    user_id: str,
    context: AuthenticatedContext = Depends(require_admin)
):
    """Remove a team member (Admin only)"""
    try:
        # Can't remove yourself
        if user_id == context.user.user_id:
            raise HTTPException(status_code=400, detail="Cannot remove yourself")

        # Verify user is in same org
        users = supabase_select("users", {
            "id": f"eq.{user_id}",
            "organization_id": f"eq.{context.user.organization_id}"
        })

        if not users:
            raise HTTPException(status_code=404, detail="User not found")

        # Soft delete
        supabase_update(
            "users",
            {"id": f"eq.{user_id}"},
            {"is_active": False}
        )

        logger.info(f"User removed: {user_id} by {context.user.email}")

        return {
            "success": True,
            "message": "Team member removed"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Remove user error: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove team member")


# ==================== Organization Routes ====================

@router.get("/organization")
async def get_organization(
    context: AuthenticatedContext = Depends(get_current_user)
):
    """Get organization details"""
    try:
        orgs = supabase_select("organizations", {
            "id": f"eq.{context.user.organization_id}"
        })

        if not orgs:
            raise HTTPException(status_code=404, detail="Organization not found")

        org = orgs[0]

        # Get stats
        stats = supabase_rpc("get_organization_stats", {
            "p_org_id": context.user.organization_id
        })

        return {
            "organization": org,
            "stats": stats[0] if stats else {}
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get org error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get organization")


@router.patch("/organization")
async def update_organization(
    name: Optional[str] = None,
    billing_email: Optional[str] = None,
    context: AuthenticatedContext = Depends(require_admin)
):
    """Update organization details (Admin only)"""
    try:
        updates = {}
        if name:
            updates["name"] = name
        if billing_email:
            updates["billing_email"] = billing_email

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        result = supabase_update(
            "organizations",
            {"id": f"eq.{context.user.organization_id}"},
            updates
        )

        return {
            "success": True,
            "message": "Organization updated",
            "organization": result[0] if result else None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update org error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update organization")


# ==================== Health Check ====================

@router.get("/health")
async def auth_health():
    """Health check for auth service"""
    return {"status": "ok", "service": "auth-v2"}
