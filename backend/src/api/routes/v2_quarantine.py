"""
Quarantine Queue API — Email Guardian (feature 006)
====================================================
GET  /api/v1/quarantine              — list quarantined emails for the brand
POST /api/v1/quarantine/{id}/promote — promote to support ticket
POST /api/v1/quarantine/{id}/discard — discard and mark as reviewed
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query

from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
from src.lib.supabase_client import supabase_select, supabase_update

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quarantine", tags=["Quarantine"])


def _get_brand_for_tenant(tenant_id: str) -> Optional[dict]:
    """Resolve tenant's primary brand; prefers active+Gmail connected.
    Falls back to shopify_domain lookup for brands created before tenant_id was linked."""
    brands = supabase_select("brands", {"tenant_id": f"eq.{tenant_id}", "is_active": "is.true"})
    if brands:
        gmail_brands = [b for b in brands if b.get("gmail_connected")]
        return gmail_brands[0] if gmail_brands else brands[0]
    brands = supabase_select("brands", {"tenant_id": f"eq.{tenant_id}", "gmail_connected": "is.true"})
    if brands:
        return brands[0]
    # Fallback: match via shopify_domain in tenants table (tenant-specific, not global)
    tenants = supabase_select("tenants", {"id": f"eq.{tenant_id}"})
    if tenants:
        shopify_domain = tenants[0].get("shopify_domain")
        if shopify_domain:
            brands = supabase_select("brands", {"shopify_domain": f"eq.{shopify_domain}"})
            if brands:
                return brands[0]
    return None


# ── T013: GET /quarantine ──────────────────────────────────────────────────────

@router.get("")
async def list_quarantine(
    tenant: TenantContext = Depends(get_current_tenant),
    status: str = Query("pending", description="Filter by status: pending|promoted|discarded|expired"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List quarantined emails for the authenticated brand. Lazily expires stale records."""
    brand = _get_brand_for_tenant(tenant.tenant_id)
    if not brand:
        raise HTTPException(status_code=404, detail="No brand found for this tenant")
    brand_id = brand["id"]

    # Lazy expiry: mark overdue pending records as expired before responding
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        supabase_update(
            "email_quarantine",
            {"brand_id": f"eq.{brand_id}", "status": "eq.pending", "expires_at": f"lt.{now_iso}"},
            {"status": "expired"},
        )
    except Exception as e:
        logger.warning(f"[Quarantine] Lazy expiry update failed (non-blocking): {e}")

    params = {
        "brand_id": f"eq.{brand_id}",
        "status":   f"eq.{status}",
        "order":    "created_at.desc",
        "limit":    str(limit),
        "offset":   str(offset),
    }
    items = supabase_select("email_quarantine", params)

    # Count total pending for the badge
    pending_items = supabase_select("email_quarantine", {"brand_id": f"eq.{brand_id}", "status": "eq.pending"})
    pending_count = len(pending_items)

    return {
        "items":   items,
        "total":   len(items),
        "pending": pending_count,
    }


# ── T014: POST /quarantine/{id}/promote ───────────────────────────────────────

@router.post("/{quarantine_id}/promote")
async def promote_quarantine(
    quarantine_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Promote a quarantined email to a support ticket."""
    brand = _get_brand_for_tenant(tenant.tenant_id)
    if not brand:
        raise HTTPException(status_code=404, detail="No brand found for this tenant")
    brand_id = brand["id"]

    rows = supabase_select("email_quarantine", {"id": f"eq.{quarantine_id}", "brand_id": f"eq.{brand_id}"})
    if not rows:
        raise HTTPException(status_code=404, detail="Quarantine record not found")

    q = rows[0]
    if q.get("status") != "pending":
        raise HTTPException(status_code=404, detail="Email already actioned")

    now_iso = datetime.now(timezone.utc).isoformat()

    # Run through the full message processor pipeline so the AI analysis
    # and email reply happen exactly as if the email had passed filtering normally.
    try:
        from src.workers.message_processor import message_processor as _mp
        payload = {
            "channel":          "email",
            "customer_email":   q.get("sender_email", ""),
            "customer_name":    q.get("sender_email", ""),
            "subject":          q.get("subject", "(no subject)"),
            "content":          q.get("body_preview", ""),
            "gmail_thread_id":  q.get("thread_id"),
            "email_category":   "support",
            "sender_type":      "human",
            "auto_reply_enabled": True,
            "store_id":         brand_id,
        }
        result = await _mp.process_message("email_incoming", payload)
        ticket_id = result.get("ticket_id") if result else None
    except Exception as e:
        logger.error(f"[Quarantine] Message processing failed for {quarantine_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to process promoted email")

    # Mark quarantine record as promoted
    supabase_update(
        "email_quarantine",
        {"id": f"eq.{quarantine_id}"},
        {"status": "promoted", "actioned_by": tenant.email, "actioned_at": now_iso},
    )

    logger.info(f"[Quarantine] Promoted {quarantine_id} → ticket {ticket_id} by {tenant.email}")
    return {"success": True, "ticket_id": ticket_id, "message": "Email promoted to ticket"}


# ── T015: POST /quarantine/{id}/discard ───────────────────────────────────────

@router.post("/{quarantine_id}/discard")
async def discard_quarantine(
    quarantine_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Discard a quarantined email without creating a ticket."""
    brand = _get_brand_for_tenant(tenant.tenant_id)
    if not brand:
        raise HTTPException(status_code=404, detail="No brand found for this tenant")
    brand_id = brand["id"]

    rows = supabase_select("email_quarantine", {"id": f"eq.{quarantine_id}", "brand_id": f"eq.{brand_id}"})
    if not rows:
        raise HTTPException(status_code=404, detail="Quarantine record not found")

    if rows[0].get("status") != "pending":
        raise HTTPException(status_code=404, detail="Email already actioned")

    now_iso = datetime.now(timezone.utc).isoformat()
    supabase_update(
        "email_quarantine",
        {"id": f"eq.{quarantine_id}"},
        {"status": "discarded", "actioned_by": tenant.email, "actioned_at": now_iso},
    )

    logger.info(f"[Quarantine] Discarded {quarantine_id} by {tenant.email}")
    return {"success": True, "message": "Email discarded"}
