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
from urllib.parse import quote

from src.services.brand_gmail_service import brand_gmail_service
from src.lib.supabase_client import supabase_select, supabase_update
from src.api.middleware.tenant_auth import get_current_tenant, require_tenant_admin, TenantContext

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/brands", tags=["Gmail"])

EMAIL_AI_PROCESSING_CONSENT_VERSION = "2026-11-28-v1"


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

    if not brand.get("is_active", True):
        raise HTTPException(status_code=409, detail="This store is inactive. Reactivate it before setting up email.")

    # Keep legacy polling active until Google verification, send OAuth, and
    # merchant activation. Allocate the token with compare-and-set so two tabs
    # opening setup simultaneously cannot return different live addresses.
    token = brand.get("inbound_email_token")
    update_filter = {
        "id": f"eq.{brand_id}",
        "tenant_id": f"eq.{tenant.tenant_id}",
        "is_active": "eq.true",
    }
    now = datetime.now(timezone.utc).isoformat()

    if token:
        persisted = supabase_update("brands", update_filter, {
            "inbound_email_enabled": True,
            "updated_at": now,
        })
        if persisted:
            brand = persisted
            token = persisted.get("inbound_email_token") or token
        else:
            # The row may have changed between the ownership read and update.
            brand = _get_owned_brand(brand_id, tenant)
            if not brand.get("is_active", True):
                raise HTTPException(status_code=409, detail="This store is inactive. Reactivate it before setting up email.")
            token = brand.get("inbound_email_token")
            if not token or not brand.get("inbound_email_enabled"):
                raise HTTPException(status_code=409, detail="The forwarding setup changed. Refresh and try again.")
    else:
        for _ in range(3):
            candidate = secrets.token_hex(20)
            updates = {
                "inbound_email_token": candidate,
                "inbound_email_enabled": True,
                "inbound_email_verified_at": None,
                "inbound_email_verification_code": None,
                "inbound_email_verification_link": None,
                "inbound_email_verification_code_at": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                # PostgREST's is.null filter makes this an atomic first-writer
                # wins operation. A concurrent request will update zero rows.
                persisted = supabase_update(
                    "brands",
                    {**update_filter, "inbound_email_token": "is.null"},
                    updates,
                )
            except Exception as exc:
                # A cryptographically improbable token collision is protected
                # by the database's unique partial index; retry only that case.
                error_text = str(exc).lower()
                if "409" not in error_text and "duplicate key" not in error_text:
                    raise
                continue

            if persisted and persisted.get("inbound_email_token"):
                brand = persisted
                token = persisted["inbound_email_token"]
                break

            # Another setup request won the race. Always return the persisted
            # winner's token; never expose our losing candidate to the client.
            brand = _get_owned_brand(brand_id, tenant)
            if not brand.get("is_active", True):
                raise HTTPException(status_code=409, detail="This store is inactive. Reactivate it before setting up email.")
            token = brand.get("inbound_email_token")
            if token:
                break

        if not token:
            raise HTTPException(
                status_code=503,
                detail="Could not allocate a forwarding address. Please try again.",
            )

    return {
        "success": True,
        "forwarding_address": f"{os.getenv('INBOUND_EMAIL_LOCAL_PART', 'support').strip().lower()}+{token}@{domain}",
        "ingestion_mode": brand.get("email_ingestion_mode", "gmail_polling"),
        "gmail_send_connected": bool(brand.get("gmail_send_connected")),
        "gmail_email": brand.get("gmail_send_email"),
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
    verification_link = brand.get("inbound_email_verification_link")
    verification_details_available = bool(
        (code or verification_link) and brand.get("inbound_email_verification_code_at")
    )
    return {
        "legacy_gmail_connected": False,
        "legacy_gmail_email": None,
        "forwarding_address": (
            f"{os.getenv('INBOUND_EMAIL_LOCAL_PART', 'support').strip().lower()}+{brand.get('inbound_email_token')}@{domain}"
            if brand.get("inbound_email_token") and domain else None
        ),
        "verified": bool(brand.get("inbound_email_verified_at")),
        "verification_code": code if verification_details_available and code else None,
        "verification_link": verification_link if verification_details_available else None,
        "gmail_send_connected": bool(brand.get("gmail_send_connected")),
        "gmail_email": brand.get("gmail_send_email"),
        "ai_processing_consent_recorded": (
            bool(brand.get("email_ai_processing_consent_at"))
            and brand.get("email_ai_processing_consent_version") == EMAIL_AI_PROCESSING_CONSENT_VERSION
        ),
        "ai_processing_consent_at": brand.get("email_ai_processing_consent_at"),
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
    link = brand.get("inbound_email_verification_link")
    if not (code or link) or not code_at:
        raise HTTPException(
            status_code=409,
            detail="Waiting for Google's forwarding confirmation email. Refresh after it reaches tResolv.",
        )
    supabase_update("brands", {"id": f"eq.{brand_id}"}, {
        "inbound_email_verified_at": datetime.now(timezone.utc).isoformat(),
        "inbound_email_verification_code": None,
        "inbound_email_verification_link": None,
        "inbound_email_verification_code_at": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"success": True, "verified": True}


@router.post("/{brand_id}/email-forwarding/consent")
async def record_email_ai_processing_consent(
    brand_id: str,
    tenant: TenantContext = Depends(require_tenant_admin),
):
    """Record a tenant admin's explicit consent for forwarded email processing."""
    brand = _get_owned_brand(brand_id, tenant)
    if not brand.get("inbound_email_token"):
        raise HTTPException(status_code=409, detail="Create the forwarding address before recording consent.")
    accepted_at = datetime.now(timezone.utc).isoformat()
    supabase_update("brands", {"id": f"eq.{brand_id}"}, {
        "email_ai_processing_consent_at": accepted_at,
        "email_ai_processing_consent_version": EMAIL_AI_PROCESSING_CONSENT_VERSION,
        "updated_at": accepted_at,
    })
    return {
        "success": True,
        "consent_version": EMAIL_AI_PROCESSING_CONSENT_VERSION,
        "consent_recorded_at": accepted_at,
    }


@router.post("/{brand_id}/email-forwarding/activate")
async def activate_email_forwarding(brand_id: str, tenant: TenantContext = Depends(require_tenant_admin)):
    """Activate only after the forwarding address and send-only Gmail are ready."""
    brand = _get_owned_brand(brand_id, tenant)
    if not brand.get("inbound_email_token"):
        raise HTTPException(status_code=409, detail="Create the forwarding address first")
    if not brand.get("inbound_email_verified_at"):
        raise HTTPException(status_code=409, detail="Verify the forwarding address in Gmail first")
    if not brand.get("gmail_send_connected") or not brand.get("gmail_send_token"):
        raise HTTPException(status_code=409, detail="Connect Gmail for sending only first")
    if (
        not brand.get("email_ai_processing_consent_at")
        or brand.get("email_ai_processing_consent_version") != EMAIL_AI_PROCESSING_CONSENT_VERSION
    ):
        raise HTTPException(
            status_code=409,
            detail="Review and accept the email AI-processing consent before activating forwarding.",
        )
    supabase_update("brands", {"id": f"eq.{brand_id}"}, {
        "inbound_email_enabled": True,
        "email_ingestion_mode": "forwarding",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"success": True, "ingestion_mode": "forwarding"}


@router.post("/{brand_id}/email-forwarding/pause")
async def pause_email_forwarding(brand_id: str, tenant: TenantContext = Depends(require_tenant_admin)):
    """Legacy Gmail polling is retired; forwarding is the only inbound email path."""
    _get_owned_brand(brand_id, tenant)
    raise HTTPException(
        status_code=410,
        detail="Legacy Gmail inbox sync is retired. Keep merchant-configured forwarding enabled for incoming email.",
    )


@router.get("/{brand_id}/gmail/send-auth-url")
async def get_gmail_send_auth_url(
    brand_id: str,
    return_to: str = "brands",
    tenant: TenantContext = Depends(require_tenant_admin),
):
    """Start send-only Gmail OAuth for forwarding-mode brands."""
    brand = _get_owned_brand(brand_id, tenant)
    if not brand.get("inbound_email_enabled") or not brand.get("inbound_email_token"):
        raise HTTPException(
            status_code=409,
            detail="Create the forwarding address before connecting Gmail send access",
        )
    if not brand.get("inbound_email_verified_at"):
        raise HTTPException(
            status_code=409,
            detail="Verify the forwarding address in Gmail before connecting send access",
        )
    try:
        return {"auth_url": brand_gmail_service.get_send_auth_url(brand_id, return_to=return_to)}
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{brand_id}/gmail/auth-url")
async def get_gmail_auth_url(
    brand_id: str,
    tenant: TenantContext = Depends(require_tenant_admin),
):
    """Retired endpoint. Gmail inbox reading is no longer supported."""
    _get_owned_brand(brand_id, tenant)
    raise HTTPException(
        status_code=410,
        detail="Gmail inbox access is retired. Use merchant-configured forwarding and Gmail send-only connection.",
    )


@router.get("/gmail/callback")
async def gmail_oauth_callback(code: str = None, state: str = None, error: str = None):
    """
    Google OAuth callback — NO user auth here (Google drives the redirect).
    The signed state token proves which brand initiated the OAuth.
    Tampering with state → verification fails → error redirect, no tokens saved.
    """
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
    return_path = brand_gmail_service.get_return_path(state) if state else "/brands"

    if error:
        logger.warning(f"[BrandGmail] OAuth denied by user: {error}")
        return RedirectResponse(f"{frontend_url}{return_path}?gmail_error={quote(error, safe='')}")

    if not code or not state:
        return RedirectResponse(f"{frontend_url}{return_path}?gmail_error=missing_params")

    result = await brand_gmail_service.handle_callback(state=state, code=code)

    if result["success"]:
        email = result.get("email", "")
        return RedirectResponse(
            f"{frontend_url}{return_path}?gmail_connected=1&email={quote(email, safe='')}"
        )

    error_code = result.get("error", "callback_failed")
    if error_code == "invalid_or_expired_state":
        logger.warning("[BrandGmail] OAuth callback rejected — state invalid or expired")
    return RedirectResponse(f"{frontend_url}{return_path}?gmail_error={quote(error_code, safe='')}")


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
    # Only the send-only Gmail API connection remains supported.
    send_connected = bool(brand.get("gmail_send_connected"))
    send_email = brand.get("gmail_send_email")
    return {
        "connected": send_connected,
        "email": send_email,
        "needs_reconnect": (not send_connected) and bool(send_email),
        "send_connected": send_connected,
        "send_email": send_email,
    }
