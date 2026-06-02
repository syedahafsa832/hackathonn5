"""
Brand Gmail OAuth Routes
========================
Endpoints for connecting/disconnecting a Gmail inbox per brand.
All brand-specific routes require tenant auth and verify brand ownership.
The /gmail/callback route is unauthenticated (Google redirects to it) but
the signed state token is the proof of which tenant initiated the OAuth.
"""
import os
import logging
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import RedirectResponse

from src.services.brand_gmail_service import brand_gmail_service
from src.lib.supabase_client import supabase_select, supabase_update
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/brands", tags=["Gmail"])


def _get_owned_brand(brand_id: str, tenant: TenantContext) -> dict:
    """Return the brand if it exists and belongs to this tenant; raise 404 otherwise.
    Always raises 404 (never 403) so we don't confirm the resource exists to wrong tenant."""
    brands = supabase_select("brands", {"id": f"eq.{brand_id}"})
    if not brands:
        raise HTTPException(status_code=404, detail="Brand not found")
    brand = brands[0]
    brand_tenant = brand.get("tenant_id")
    if not brand_tenant or brand_tenant != tenant.tenant_id:
        raise HTTPException(status_code=404, detail="Brand not found")
    return brand


@router.get("/{brand_id}/gmail/auth-url")
async def get_gmail_auth_url(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Return the Google OAuth consent URL for this brand (tenant-scoped)."""
    _get_owned_brand(brand_id, tenant)
    try:
        auth_url = brand_gmail_service.get_auth_url(brand_id)
        return {"auth_url": auth_url}
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/gmail/callback")
async def gmail_oauth_callback(code: str = None, state: str = None, error: str = None):
    """
    Google OAuth callback — NO user auth here (Google drives the redirect).
    The signed state token proves which brand initiated the OAuth.
    Tampering with state → verification fails → error redirect, no tokens saved.
    """
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")

    if error:
        logger.warning(f"[BrandGmail] OAuth denied by user: {error}")
        return RedirectResponse(f"{frontend_url}/brands?gmail_error={error}")

    if not code or not state:
        return RedirectResponse(f"{frontend_url}/brands?gmail_error=missing_params")

    result = await brand_gmail_service.handle_callback(state=state, code=code)

    if result["success"]:
        email = result.get("email", "")
        return RedirectResponse(f"{frontend_url}/settings?gmail_connected=1&email={email}")

    error_code = result.get("error", "callback_failed")
    if error_code == "invalid_or_expired_state":
        logger.warning("[BrandGmail] OAuth callback rejected — state invalid or expired")
    return RedirectResponse(f"{frontend_url}/settings?gmail_error={error_code}")


@router.post("/{brand_id}/gmail/disconnect")
async def disconnect_gmail(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Disconnect Gmail from a brand (tenant-scoped)."""
    _get_owned_brand(brand_id, tenant)
    brand_gmail_service.disconnect(brand_id)
    return {"success": True, "message": "Gmail disconnected"}


@router.get("/{brand_id}/gmail/status")
async def gmail_status(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Check Gmail connection status for a brand (tenant-scoped)."""
    brand = _get_owned_brand(brand_id, tenant)
    return {
        "connected": bool(brand.get("gmail_connected")),
        "email":     brand.get("gmail_email"),
    }
