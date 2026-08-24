"""
Tenant Authentication Middleware
================================
Validates the authenticated Supabase Auth session (or, for a transition
window, an already-issued legacy tenant JWT) and attaches tenant context to
requests. All protected routes use this to ensure tenant isolation.
"""
import logging
from typing import Optional
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from src.services.auth_service import auth_service, FoundingCohortFullError
from src.services.supabase_auth_service import supabase_auth_service

logger = logging.getLogger(__name__)

# Security scheme for Swagger UI
security = HTTPBearer(auto_error=False)


class TenantContext:
    """
    Holds the authenticated tenant's context.
    Attached to request.state for use in route handlers.
    """
    def __init__(self, tenant_id: str, email: str):
        self.tenant_id = tenant_id
        self.email = email

    def __repr__(self):
        return f"TenantContext(tenant_id={self.tenant_id}, email={self.email})"


async def get_current_tenant(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> TenantContext:
    """
    Dependency that verifies the bearer token and resolves it to a tenant.

    Accepts two token shapes:
    - A Supabase Auth access token (the normal case since the auth
      migration) — verified via JWKS/shared-secret against Supabase, then
      mapped to a tenant by supabase_user_id/email.
    - A legacy pre-migration tenant JWT (marked by a "type": "access" claim,
      same signing secret) — its `sub` IS the tenant_id directly, as before.
      Purely a transition allowance for sessions issued before this
      migration shipped; nothing issues these anymore.

    Usage in routes:
        @router.get("/actions")
        async def get_actions(tenant: TenantContext = Depends(get_current_tenant)):
            # tenant.tenant_id is guaranteed to be valid
            actions = await get_actions_for_tenant(tenant.tenant_id)
    """
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = credentials.credentials
    payload = supabase_auth_service.verify_jwt(token)

    if not payload:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"}
        )

    if payload.get("type") == "access":
        # Legacy pre-migration token — sub is already the tenant_id.
        tenant_id = payload.get("sub")
        email = payload.get("email")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")
    else:
        supabase_user_id = payload.get("sub")
        email = payload.get("email")
        if not supabase_user_id or not email:
            raise HTTPException(status_code=401, detail="Invalid token payload")

        try:
            tenant_row = await auth_service.resolve_or_create_tenant_for_supabase_user(supabase_user_id, email)
        except FoundingCohortFullError:
            raise HTTPException(status_code=403, detail={
                "error": "founding_cohort_full",
                "message": "Our Founding 20 program is full. Join the waitlist instead.",
                "waitlist_url": "https://tresolv.online/waitlist",
            })

        if not tenant_row:
            raise HTTPException(status_code=401, detail="Account not found")
        if not tenant_row.get("is_active"):
            raise HTTPException(status_code=403, detail="Account is disabled")

        tenant_id = tenant_row["id"]
        email = tenant_row["email"]

    # Create tenant context
    tenant = TenantContext(tenant_id=tenant_id, email=email)

    # Attach to request state for logging/middleware access
    request.state.tenant = tenant

    return tenant


async def get_optional_tenant(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Optional[TenantContext]:
    """
    Optional authentication - returns None if no valid token.
    Use for endpoints that work both authenticated and unauthenticated.
    """
    if not credentials:
        return None

    try:
        return await get_current_tenant(request, credentials)
    except HTTPException:
        return None


def require_shopify_connected(tenant: TenantContext = Depends(get_current_tenant)):
    """
    Dependency that requires the tenant to have Shopify connected.
    Use for routes that need Shopify API access.
    """
    async def check_shopify():
        tenant_data = await auth_service.get_tenant(tenant.tenant_id)
        if not tenant_data or not tenant_data.get("shopify_connected"):
            raise HTTPException(
                status_code=400,
                detail="Shopify store not connected. Please connect your store first."
            )
        return tenant

    return check_shopify


class TenantFilter:
    """
    Helper class for building tenant-filtered database queries.

    Usage:
        filter = TenantFilter(tenant.tenant_id)
        actions = supabase_select("actions", filter.params(status="eq.pending"))
    """
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id

    def params(self, **kwargs) -> dict:
        """Build query params with tenant_id always included."""
        params = {"tenant_id": f"eq.{self.tenant_id}"}
        params.update(kwargs)
        return params

    def __repr__(self):
        return f"TenantFilter(tenant_id={self.tenant_id})"
