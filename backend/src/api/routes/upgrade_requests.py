"""
Upgrade Requests API (item 7)
================================
Customer-facing half of the manual-activation upgrade flow: list plan tiers
(reusing plan_service.PLAN_LIMITS as the single source of truth, so this
never drifts from what check_limit() actually enforces) and submit a
"Request Upgrade" form. No Stripe, no real checkout — this only creates a
pending row for the platform admin to activate by hand after confirming
payment out-of-band. See platform_admin.py for the admin-side endpoints.
"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr, Field
from typing import Optional

from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
from src.lib.supabase_client import supabase_insert
from src.services.plan_service import PLAN_LIMITS, FOUNDING_PAID_CAP, get_founding_slots_remaining

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Upgrade Requests"])

# founding_free is legacy (see plan_service.py) — not offered to new requests.
_VISIBLE_PLANS = ["free", "starter", "growth", "enterprise"]


class UpgradeRequestBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    brand: Optional[str] = Field(None, max_length=200)
    plan: str
    transaction_reference: Optional[str] = Field(None, max_length=200)


@router.get("/plans")
async def list_plans():
    """Public plan tier data for the pricing/upgrade page."""
    return {
        "plans": [
            {"id": pid, **PLAN_LIMITS[pid]}
            for pid in _VISIBLE_PLANS if pid in PLAN_LIMITS
        ]
    }


@router.get("/founding-status")
async def founding_status():
    """Whether the "Founding price" badge should still be shown on the
    pricing page — mirrors the landing page's "first 20 brands" framing.
    Public: no info here beyond a remaining-slot count."""
    remaining = get_founding_slots_remaining()
    return {"slots_remaining": remaining, "cap": FOUNDING_PAID_CAP, "active": remaining > 0}


@router.post("/upgrade-requests")
async def submit_upgrade_request(
    body: UpgradeRequestBody,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Create a pending upgrade request. No billing happens here."""
    if body.plan not in PLAN_LIMITS:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {body.plan}")
    try:
        result = supabase_insert("upgrade_requests", {
            "tenant_id": tenant.tenant_id,
            "name": body.name,
            "email": body.email,
            "brand": body.brand,
            "requested_plan": body.plan,
            "transaction_reference": body.transaction_reference,
            "status": "pending",
        })
        return {"success": True, "request": result}
    except Exception as e:
        logger.error(f"[UpgradeRequests] submit failed for tenant {tenant.tenant_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to submit upgrade request")
