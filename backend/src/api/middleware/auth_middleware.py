"""
Authentication Middleware
=========================
Provides authentication and authorization for API routes.
Supports both Supabase Auth and legacy JWT authentication.
"""

import os
import logging
from typing import Optional, List
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from src.services.supabase_auth_service import (
    supabase_auth_service,
    UserContext,
    OrganizationContext,
    UserRole
)

logger = logging.getLogger(__name__)

# Security scheme
security = HTTPBearer(auto_error=False)


# ==================== Context Models ====================

class AuthenticatedContext(BaseModel):
    """Full authenticated context for requests"""
    user: UserContext
    organization: Optional[OrganizationContext] = None
    brand_ids: List[str] = []

    class Config:
        arbitrary_types_allowed = True


# ==================== Dependencies ====================

async def get_optional_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Optional[AuthenticatedContext]:
    """
    Optional authentication dependency.
    Returns None if no valid auth, does not raise exception.
    """
    if not credentials:
        return None

    try:
        token = credentials.credentials

        # Verify JWT
        payload = supabase_auth_service.verify_jwt(token)
        if not payload:
            return None

        # Get user's Supabase auth ID
        supabase_auth_id = payload.get("sub")
        if not supabase_auth_id:
            return None

        # Get full user context from database
        user_context = await supabase_auth_service.get_user_context(supabase_auth_id)

        # v1 token fallback: sub is a tenant_id, not a Supabase auth UUID
        if not user_context:
            user_context = await supabase_auth_service.get_tenant_by_id(supabase_auth_id)
            if not user_context:
                return None
            # v1 tenants don't have an organizations table entry — skip org lookup
            return AuthenticatedContext(
                user=user_context,
                organization=None,
                brand_ids=user_context.brands
            )

        # Get organization context
        org_context = await supabase_auth_service.get_organization_context(
            user_context.organization_id
        )

        return AuthenticatedContext(
            user=user_context,
            organization=org_context,
            brand_ids=user_context.brands
        )

    except Exception as e:
        logger.error(f"Auth error: {e}")
        return None


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> AuthenticatedContext:
    """
    Required authentication dependency.
    Raises 401 if not authenticated.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"}
        )

    context = await get_optional_auth(request, credentials)

    if not context:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"}
        )

    return context


async def require_admin(
    context: AuthenticatedContext = Depends(get_current_user)
) -> AuthenticatedContext:
    """Require admin role"""
    if not UserRole.is_admin(context.user.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return context


async def require_agent_or_admin(
    context: AuthenticatedContext = Depends(get_current_user)
) -> AuthenticatedContext:
    """Require agent or admin role"""
    if not UserRole.can_manage(context.user.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent or admin access required"
        )
    return context


def require_permission(permission: str):
    """
    Factory for permission-based dependencies.

    Usage:
        @router.post("/tickets")
        async def create_ticket(
            context: AuthenticatedContext = Depends(require_permission("tickets:write"))
        ):
            ...
    """
    async def check_permission(
        context: AuthenticatedContext = Depends(get_current_user)
    ) -> AuthenticatedContext:
        has_permission = await supabase_auth_service.check_permission(
            context.user, permission
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {permission}"
            )
        return context

    return check_permission


def require_brand_access(brand_id_param: str = "brand_id"):
    """
    Factory for brand-scoped access control.

    Usage:
        @router.get("/brands/{brand_id}/tickets")
        async def get_brand_tickets(
            brand_id: str,
            context: AuthenticatedContext = Depends(require_brand_access("brand_id"))
        ):
            ...
    """
    async def check_brand_access(
        request: Request,
        context: AuthenticatedContext = Depends(get_current_user)
    ) -> AuthenticatedContext:
        # Get brand_id from path or query
        brand_id = request.path_params.get(brand_id_param) or request.query_params.get(brand_id_param)

        if not brand_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Brand ID required"
            )

        # Admins can access all brands in their org
        if UserRole.is_admin(context.user.role):
            # Verify brand belongs to user's org
            from src.lib.supabase_client import supabase_select
            brands = supabase_select("brands", {
                "id": f"eq.{brand_id}",
                "organization_id": f"eq.{context.user.organization_id}"
            })
            if not brands:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Brand not found"
                )
            return context

        # Check if user has access to this brand
        if brand_id not in context.brand_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this brand"
            )

        return context

    return check_brand_access


# ==================== Helper Functions ====================

def get_brand_filter(context: AuthenticatedContext) -> dict:
    """
    Get brand filter for database queries.

    Returns filter that limits results to user's accessible brands.
    """
    if UserRole.is_admin(context.user.role):
        # Admin sees all brands in org
        return {"organization_id": f"eq.{context.user.organization_id}"}
    else:
        # Others see only assigned brands
        brand_ids = ",".join(context.brand_ids) if context.brand_ids else ""
        return {"id": f"in.({brand_ids})"}


def get_org_filter(context: AuthenticatedContext) -> dict:
    """Get organization filter for queries"""
    return {"organization_id": f"eq.{context.user.organization_id}"}


# ==================== Backward Compatibility ====================

# Legacy context for gradual migration
class LegacyTenantContext:
    """
    Backward-compatible tenant context.
    Maps new auth to old tenant_id pattern.
    """
    def __init__(self, auth_context: AuthenticatedContext):
        self.tenant_id = auth_context.user.organization_id
        self.user_id = auth_context.user.user_id
        self.email = auth_context.user.email
        self.role = auth_context.user.role
        self.brands = auth_context.brand_ids


async def get_legacy_tenant(
    context: AuthenticatedContext = Depends(get_current_user)
) -> LegacyTenantContext:
    """
    Provide legacy tenant context for gradual migration.

    Use this to maintain compatibility with existing code
    while transitioning to new auth system.
    """
    return LegacyTenantContext(context)
