"""
Platform Admin API
====================
Read-only, cross-tenant visibility for the platform super-admin (see
plan_service.is_super_admin). Separate from admin.py, which is per-tenant
operational tooling (AI mode, GDPR delete, etc.) scoped by the caller's own
brand_ids — this router intentionally sees across ALL tenants, so every
route must gate on is_super_admin, not just "is authenticated."

No destructive actions here — list/read only.
"""
import logging
from fastapi import APIRouter, HTTPException, Depends

from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
from src.lib.supabase_client import supabase_select, supabase_insert
from src.services.plan_service import is_super_admin, PLAN_LIMITS, PAID_PLANS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Platform Admin"])


async def require_platform_admin(tenant: TenantContext = Depends(get_current_tenant)) -> TenantContext:
    """403s any non-admin JWT before a route body ever runs."""
    if not is_super_admin(tenant.email):
        raise HTTPException(status_code=403, detail="Admin access required")
    return tenant


@router.get("/tenants")
async def list_tenants(tenant: TenantContext = Depends(require_platform_admin)):
    """Every tenant, with plan, signup date, per-brand connection status, and
    ticket volume — everything the platform admin overview needs in one call."""
    try:
        tenants = supabase_select("tenants", {
            "select": "id,email,company_name,plan,is_active,created_at,last_login_at",
            "order": "created_at.desc",
        }) or []
        brands = supabase_select("brands", {
            "select": "id,tenant_id,name,gmail_connected,shopify_connected,is_active",
        }) or []
        tickets = supabase_select("tickets", {"select": "id,brand_id"}) or []

        brand_id_to_tenant = {b["id"]: b.get("tenant_id") for b in brands}
        ticket_count_by_tenant = {}
        for t in tickets:
            tid = brand_id_to_tenant.get(t.get("brand_id"))
            if tid:
                ticket_count_by_tenant[tid] = ticket_count_by_tenant.get(tid, 0) + 1

        brands_by_tenant = {}
        for b in brands:
            brands_by_tenant.setdefault(b.get("tenant_id"), []).append({
                "id": b["id"],
                "name": b.get("name"),
                "gmail_connected": bool(b.get("gmail_connected")),
                "shopify_connected": bool(b.get("shopify_connected")),
                "is_active": bool(b.get("is_active")),
            })

        result = []
        for t in tenants:
            tid = t["id"]
            result.append({
                "id": tid,
                "email": t.get("email"),
                "company_name": t.get("company_name"),
                "plan": t.get("plan"),
                "plan_label": PLAN_LIMITS.get((t.get("plan") or "").lower(), {}).get("label", t.get("plan")),
                "is_active": bool(t.get("is_active")),
                "is_super_admin": is_super_admin(t.get("email")),
                "created_at": t.get("created_at"),
                "last_login_at": t.get("last_login_at"),
                "brands": brands_by_tenant.get(tid, []),
                "ticket_count": ticket_count_by_tenant.get(tid, 0),
            })
        return {"tenants": result, "count": len(result)}
    except Exception as e:
        logger.error(f"[PlatformAdmin] list_tenants failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to load tenants")


@router.get("/upgrade-requests")
async def list_upgrade_requests(tenant: TenantContext = Depends(require_platform_admin)):
    """Pending manual-activation upgrade requests (see item 7)."""
    try:
        rows = supabase_select("upgrade_requests", {"order": "created_at.desc"}) or []
        return {"requests": rows, "count": len(rows)}
    except Exception as e:
        logger.error(f"[PlatformAdmin] list_upgrade_requests failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to load upgrade requests")


@router.post("/upgrade-requests/{request_id}/activate")
async def activate_upgrade_request(request_id: str, tenant: TenantContext = Depends(require_platform_admin)):
    """Manually activate a pending upgrade request: sets the target tenant's
    plan and marks the request resolved. This is the one write this router
    performs — deliberate, not part of the read-only tenant listing above."""
    from src.lib.supabase_client import supabase_update
    try:
        reqs = supabase_select("upgrade_requests", {"id": f"eq.{request_id}"})
        if not reqs:
            raise HTTPException(status_code=404, detail="Upgrade request not found")
        req = reqs[0]
        if req.get("status") == "activated":
            return {"success": True, "message": "Already activated"}

        target_tenant_id = req.get("tenant_id")
        plan = req.get("requested_plan")
        previous_plan = None
        if target_tenant_id and plan in PLAN_LIMITS:
            _prior = supabase_select("tenants", {"id": f"eq.{target_tenant_id}", "select": "plan"})
            previous_plan = _prior[0].get("plan") if _prior else None
            from datetime import datetime, timezone
            update = {"plan": plan}
            # Paid plans expire 30 days after activation (plan_service._resolve_plan);
            # free/trial/founding_free don't, so no activation timestamp needed for those.
            if plan in PAID_PLANS:
                update["plan_activated_at"] = datetime.now(timezone.utc).isoformat()
            # Founding price badge only applies to starter/growth — scale has
            # no founding price (see FOUNDING_PAID_CAP in plan_service.py).
            # Only claim a slot the first time; a lapsed-and-reactivated
            # tenant keeps their original claim, doesn't take a second one.
            if plan in ("starter", "growth"):
                existing = supabase_select("tenants", {"id": f"eq.{target_tenant_id}", "select": "founding_pricing_claimed_at"})
                if existing and not existing[0].get("founding_pricing_claimed_at"):
                    update["founding_pricing_claimed_at"] = datetime.now(timezone.utc).isoformat()
            supabase_update("tenants", {"id": f"eq.{target_tenant_id}"}, update)

        supabase_update("upgrade_requests", {"id": f"eq.{request_id}"}, {
            "status": "activated",
            "activated_by": tenant.email,
        })

        # Audit trail for this sensitive cross-tenant plan change — reuses
        # the existing audit_logs table/helper, no new schema.
        try:
            from src.services.supabase_service import supabase_service
            await supabase_service.log_audit(
                store_id=target_tenant_id, action="platform_admin.activate_upgrade_request",
                performer=tenant.email,
                metadata={"request_id": request_id, "previous_plan": previous_plan, "new_plan": plan},
            )
        except Exception as _audit_err:
            logger.warning(f"[PlatformAdmin] Audit log write failed (non-blocking): {_audit_err}")

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PlatformAdmin] activate_upgrade_request failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to activate upgrade request")
