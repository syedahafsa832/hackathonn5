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
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import RedirectResponse

from src.services.brand_gmail_service import brand_gmail_service
from src.lib.supabase_client import supabase_select, supabase_update
from src.api.middleware.tenant_auth import get_current_tenant, require_tenant_admin, TenantContext

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


@router.post("/{brand_id}/email-forwarding/setup")
async def setup_email_forwarding(
    brand_id: str,
    tenant: TenantContext = Depends(require_tenant_admin),
):
    """Create or reuse a private inbound address for this brand."""
    brand = _get_owned_brand(brand_id, tenant)
    domain = os.getenv("INBOUND_EMAIL_DOMAIN", "").strip().lower().lstrip("@")
    if not domain or "@" in domain or " " in domain:
        raise HTTPException(
            status_code=503,
            detail="Inbound email domain is not configured on the backend",
        )

    token = brand.get("inbound_email_token")
    is_new_token = not bool(token)
    if is_new_token:
        token = secrets.token_hex(20)

    updates = {
        "inbound_email_token": token,
        "inbound_email_enabled": True,
        "email_ingestion_mode": "forwarding",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if is_new_token:
        updates.update({
            "inbound_email_verified_at": None,
            "inbound_email_verification_code": None,
            "inbound_email_verification_code_at": None,
        })
    supabase_update("brands", {"id": f"eq.{brand_id}"}, updates)

    return {
        "success": True,
        "forwarding_address": f"{os.getenv('INBOUND_EMAIL_LOCAL_PART', 'support').strip().lower()}+{token}@{domain}",
        "ingestion_mode": "forwarding",
        "gmail_send_connected": bool(
            brand.get("gmail_connected") and brand.get("gmail_scope_mode") == "send_only"
        ),
        "note": "This is a routing address, not a mailbox. Forward support emails to it from the merchant's inbox.",
    }



@router.get("/{brand_id}/email-forwarding/status")
async def email_forwarding_status(
    brand_id: str,
    tenant: TenantContext = Depends(require_tenant_admin),
):
    """Return the forwarding address and a short-lived Gmail verification code."""
    brand = _get_owned_brand(brand_id, tenant)
    domain = os.getenv("INBOUND_EMAIL_DOMAIN", "").strip().lower().lstrip("@")
    code = brand.get("inbound_email_verification_code")
    code_is_available = bool(code and brand.get("inbound_email_verification_code_at"))
    return {
        "forwarding_address": (
            f"{brand.get('inbound_email_token')}@{domain}"
            if brand.get("inbound_email_token") and domain else None
        ),
        "verified": bool(brand.get("inbound_email_verified_at")),
        "verification_code": code if code_is_available else None,
        "gmail_send_connected": bool(
            brand.get("gmail_connected") and brand.get("gmail_scope_mode") == "send_only"
        ),
        "gmail_email": brand.get("gmail_email"),
        "ingestion_mode": brand.get("email_ingestion_mode", "gmail_polling"),
    }


@router.post("/{brand_id}/email-forwarding/confirm")
async def confirm_email_forwarding(
    brand_id: str,
    tenant: TenantContext = Depends(require_tenant_admin),
):
    """Confirm that the merchant completed Gmail's forwarding-address check."""
    brand = _get_owned_brand(brand_id, tenant)
    if not brand.get("inbound_email_token") or not brand.get("inbound_email_enabled"):
        raise HTTPException(status_code=409, detail="Set up forwarding first")
    code_at = brand.get("inbound_email_verification_code_at")
    code = brand.get("inbound_email_verification_code")
    if not code or not code_at:
        raise HTTPException(status_code=409, detail="Waiting for Gmail to send its forwarding confirmation code")
    supabase_update("brands", {"id": f"eq.{brand_id}"}, {
        "inbound_email_verified_at": datetime.now(timezone.utc).isoformat(),
        "inbound_email_verification_code": None,
        "inbound_email_verification_code_at": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"success": True, "verified": True}


@router.get("/{brand_id}/gmail/send-auth-url")
async def get_gmail_send_auth_url(
    brand_id: str,
    tenant: TenantContext = Depends(require_tenant_admin),
):
    """Start send-only Gmail OAuth for forwarding-mode brands."""
    brand = _get_owned_brand(brand_id, tenant)
    if brand.get("email_ingestion_mode") != "forwarding" or not brand.get("inbound_email_enabled"):
        raise HTTPException(
            status_code=409,
            detail="Set up the forwarding address before connecting Gmail send access",
        )
    try:
        return {"auth_url": brand_gmail_service.get_send_auth_url(brand_id)}
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{brand_id}/gmail/auth-url")
async def get_gmail_auth_url(
    brand_id: str,
    tenant: TenantContext = Depends(require_tenant_admin),
):
    """Return the Google OAuth consent URL for this brand (tenant-scoped)."""
    brand = _get_owned_brand(brand_id, tenant)

    # Only gate NEW connections — reconnecting/refreshing an already-connected
    # brand's Gmail must not be blocked by the cap.
    if not brand.get("gmail_connected"):
        from src.services.plan_service import check_limit, build_limit_error
        gmail_limit = check_limit(tenant.tenant_id, "gmail_accounts", email=tenant.email)
        if not gmail_limit["allowed"]:
            raise HTTPException(status_code=402, detail=build_limit_error("gmail_accounts", gmail_limit))

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
        return RedirectResponse(f"{frontend_url}/brands?gmail_connected=1&email={email}")

    error_code = result.get("error", "callback_failed")
    if error_code == "invalid_or_expired_state":
        logger.warning("[BrandGmail] OAuth callback rejected — state invalid or expired")
    return RedirectResponse(f"{frontend_url}/brands?gmail_error={error_code}")


@router.post("/{brand_id}/gmail/disconnect")
async def disconnect_gmail(
    brand_id: str,
    tenant: TenantContext = Depends(require_tenant_admin),
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
    connected = bool(brand.get("gmail_connected"))
    email = brand.get("gmail_email")
    # A revoked/expired refresh token (see brand_gmail_service._build_service)
    # flips gmail_connected to False but deliberately leaves gmail_email in
    # place — the only signal that distinguishes "never connected" from "was
    # connected, needs reconnecting". A manual disconnect() clears both, so
    # this is False there, correctly falling back to the plain not-connected
    # state.
    return {
        "connected": connected,
        "email": email,
        "needs_reconnect": (not connected) and bool(email),
    }
