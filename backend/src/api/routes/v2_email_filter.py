"""
Email Filter Settings & Logs API
=================================
GET  /api/v1/settings/email-filter   — return filter config for tenant's brand
PATCH /api/v1/settings/email-filter  — partial update (only provided fields change)
GET  /api/v1/filter-logs             — aggregated summary or paginated log list
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
from src.lib.supabase_client import supabase_select, supabase_update, supabase_insert

logger = logging.getLogger(__name__)

DEFAULT_STORE = "00000000-0000-0000-0000-000000000000"

settings_router = APIRouter(prefix="/settings", tags=["Email Filter"])
logs_router = APIRouter(tags=["Email Filter"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_brand_for_tenant(tenant_id: str) -> Optional[dict]:
    """Resolve the tenant's primary brand; prefers active+Gmail connected."""
    brands = supabase_select("brands", {"tenant_id": f"eq.{tenant_id}", "is_active": "is.true"})
    if brands:
        gmail_brands = [b for b in brands if b.get("gmail_connected")]
        return gmail_brands[0] if gmail_brands else brands[0]
    # Fallback 2: gmail-connected brand regardless of is_active
    brands = supabase_select("brands", {"tenant_id": f"eq.{tenant_id}", "gmail_connected": "is.true"})
    if brands:
        return brands[0]
    # Fallback 3: any brand linked to this tenant (catches gmail_connected=false/null)
    brands = supabase_select("brands", {"tenant_id": f"eq.{tenant_id}"})
    if brands:
        return brands[0]
    # Fallback 4: match via shopify_domain in tenants table
    tenants = supabase_select("tenants", {"id": f"eq.{tenant_id}"})
    if tenants:
        shopify_domain = tenants[0].get("shopify_domain")
        if shopify_domain:
            brands = supabase_select("brands", {"shopify_domain": f"eq.{shopify_domain}"})
            if brands:
                return brands[0]
    return None


def _get_filter_settings(store_id: str) -> dict:
    """Load filter settings row with safe defaults."""
    defaults = {
        "blocked_domains": [],
        "whitelisted_domains": [],
        "max_auto_replies": 2,
        "promotion_filter_enabled": True,
        "loop_protection_enabled": True,
        # Guardian fields (feature 006)
        "support_only_mode": True,
        "confidence_threshold": 0.75,
        "auto_reply_enabled": True,
    }
    rows = supabase_select("system_settings", {"store_id": f"eq.{store_id}"})
    if not rows and store_id != DEFAULT_STORE:
        rows = supabase_select("system_settings", {"store_id": f"eq.{DEFAULT_STORE}"})
    if rows:
        r = rows[0]
        for key in defaults:
            if key in r and r[key] is not None:
                defaults[key] = r[key]
    return defaults


# ─── Request/Response Models ──────────────────────────────────────────────────

class EmailFilterSettingsResponse(BaseModel):
    blocked_domains: List[str] = []
    whitelisted_domains: List[str] = []
    max_auto_replies: int = 2
    promotion_filter_enabled: bool = True
    loop_protection_enabled: bool = True
    # Guardian fields (feature 006)
    support_only_mode: bool = True
    confidence_threshold: float = 0.75
    auto_reply_enabled: bool = True


class EmailFilterSettingsPatch(BaseModel):
    blocked_domains: Optional[List[str]] = None
    whitelisted_domains: Optional[List[str]] = None
    max_auto_replies: Optional[int] = Field(None, ge=0, le=10)
    promotion_filter_enabled: Optional[bool] = None
    loop_protection_enabled: Optional[bool] = None
    # Guardian fields (feature 006)
    support_only_mode: Optional[bool] = None
    confidence_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    auto_reply_enabled: Optional[bool] = None


# ─── Settings Endpoints ───────────────────────────────────────────────────────

@settings_router.get("/email-filter", response_model=EmailFilterSettingsResponse)
async def get_email_filter_settings(tenant: TenantContext = Depends(get_current_tenant)):
    """Return current email filter configuration for the tenant's brand."""
    brand = _get_brand_for_tenant(tenant.tenant_id)
    store_id = brand["id"] if brand else DEFAULT_STORE
    return _get_filter_settings(store_id)


@settings_router.patch("/email-filter", response_model=EmailFilterSettingsResponse)
async def patch_email_filter_settings(
    body: EmailFilterSettingsPatch,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Partially update email filter settings. Only provided fields are changed.
    Changes take effect on the next email poll cycle (≤ 60 seconds)."""
    brand = _get_brand_for_tenant(tenant.tenant_id)
    store_id = brand["id"] if brand else DEFAULT_STORE

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return _get_filter_settings(store_id)

    existing = supabase_select("system_settings", {"store_id": f"eq.{store_id}"})
    if existing:
        supabase_update("system_settings", {"store_id": f"eq.{store_id}"}, updates)
    else:
        row = {
            "store_id": store_id,
            "ai_mode": "active",
            "blocked_domains": [],
            "whitelisted_domains": [],
            "max_auto_replies": 2,
            "promotion_filter_enabled": True,
            "loop_protection_enabled": True,
            # Guardian defaults (feature 006)
            "support_only_mode": True,
            "confidence_threshold": 0.75,
            "auto_reply_enabled": True,
        }
        row.update(updates)
        supabase_insert("system_settings", row)

    return _get_filter_settings(store_id)


# ─── Filter Logs Endpoint ─────────────────────────────────────────────────────

@logs_router.get("/filter-logs")
async def get_filter_logs(
    tenant: TenantContext = Depends(get_current_tenant),
    summary: bool = Query(False, description="Return aggregated summary for dashboard widget"),
    days: int = Query(7, ge=1, le=90, description="Lookback window in days"),
    decision: Optional[str] = Query(None, description="Filter by decision: allowed | blocked"),
    reason: Optional[str] = Query(None, description="Filter by filter_reason code"),
    limit: int = Query(50, ge=1, le=200, description="Max rows (list mode only)"),
    offset: int = Query(0, ge=0, description="Pagination offset (list mode only)"),
):
    """
    Return filter log data for this tenant's brand.

    - `?summary=true&days=7` → aggregated counts for the dashboard widget
    - Default → paginated list of individual filter decisions
    """
    brand = _get_brand_for_tenant(tenant.tenant_id)
    if not brand:
        if summary:
            return {
                "total_blocked": 0, "total_allowed": 0, "total_quarantined": 0,
                "by_reason": {}, "prevented_loops": 0, "period_days": days, "brand_id": None,
            }
        return {"logs": [], "total": 0, "has_more": False}
    brand_id = brand["id"]

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    if summary:
        # Fetch all rows in window for in-Python aggregation
        params = {
            "brand_id":   f"eq.{brand_id}",
            "created_at": f"gte.{cutoff}",
        }
        rows = supabase_select("email_filter_log", params)

        total_blocked = sum(1 for r in rows if r.get("decision") == "blocked")
        total_allowed = sum(1 for r in rows if r.get("decision") == "allowed")
        total_quarantined_log = sum(1 for r in rows if r.get("decision") == "quarantined")
        by_reason: dict = {}
        for r in rows:
            if r.get("decision") in ("blocked", "quarantined") and r.get("filter_reason"):
                key = r["filter_reason"]
                by_reason[key] = by_reason.get(key, 0) + 1

        # Live quarantine count (non-expired records in the quarantine table)
        quarantine_rows = supabase_select("email_quarantine", {
            "brand_id":   f"eq.{brand_id}",
            "created_at": f"gte.{cutoff}",
            "status":     "neq.expired",
        })
        total_quarantined = len(quarantine_rows)

        return {
            "total_blocked":     total_blocked,
            "total_allowed":     total_allowed,
            "total_quarantined": total_quarantined,
            "by_reason":         by_reason,
            "prevented_loops":   by_reason.get("loop_risk", 0),
            "period_days":       days,
            "brand_id":          brand_id,
        }

    # Paginated list mode
    params = {
        "brand_id":   f"eq.{brand_id}",
        "created_at": f"gte.{cutoff}",
        "order":      "created_at.desc",
        "limit":      str(limit),
        "offset":     str(offset),
    }
    if decision:
        params["decision"] = f"eq.{decision}"
    if reason:
        params["filter_reason"] = f"eq.{reason}"

    rows = supabase_select("email_filter_log", params)
    return {"items": rows, "limit": limit, "offset": offset}
