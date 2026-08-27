"""
Brands API Routes (v2)
======================
Uses v1 tenant JWT auth and the actual brands table schema.
Replaces the old version that referenced non-existent columns
(organization_id, slug, ai_auto_respond, etc.).
"""

import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from datetime import datetime, timezone, timedelta

from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update, supabase_delete, supabase_rpc
from src.services.shopify_service import encrypt_token
from src.agent import reply_style_presets
from src.services import reply_style_service
from src.services import shopify_import_service
from src.services import shopify_scope_service
from src.services.supabase_service import supabase_service
from src.services import email_automation_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/brands", tags=["Brands v2"])

SAFE_COLUMNS = {"id", "name", "shopify_shop_name", "shopify_domain", "shopify_connected",
                "support_email", "is_active", "gmail_email", "gmail_connected",
                "return_policy_days", "auto_approve_threshold", "created_at", "updated_at",
                "tenant_id", "exclude_digital_products", "refund_notes", "final_sale_tags",
                "agent_name", "email_signature",
                "reply_style_mode", "reply_style_preset", "reply_style_profile",
                "reply_style_reasoning", "reply_style_learn_automatically",
                "reply_style_use_uploaded_only", "reply_style_last_generated_at"}


def _strip_secrets(brand: dict) -> dict:
    return {k: v for k, v in brand.items() if k in SAFE_COLUMNS}


def _get_owned_brand(brand_id: str, tenant_id: str) -> dict:
    """Fetch a brand and verify it belongs to the current tenant.
    Raises 404 (not 403) for a brand that exists but belongs to someone
    else — an unauthorized caller shouldn't be able to distinguish 'not
    yours' from 'doesn't exist' by probing IDs."""
    brands = supabase_select("brands", {"id": f"eq.{brand_id}", "tenant_id": f"eq.{tenant_id}"})
    if not brands:
        raise HTTPException(status_code=404, detail="Brand not found")
    return brands[0]


# ==================== Request Models ====================

class CreateBrandRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: Optional[str] = None          # accepted but ignored (no slug column)
    support_email: Optional[str] = None
    shopify_shop_name: Optional[str] = None
    shopify_domain: Optional[str] = None
    shopify_access_token: Optional[str] = None


class UpdateBrandRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    support_email: Optional[str] = None
    is_active: Optional[bool] = None
    return_policy_days: Optional[int] = None
    auto_approve_threshold: Optional[float] = None
    exclude_digital_products: Optional[bool] = None
    refund_notes: Optional[str] = None
    final_sale_tags: Optional[list[str]] = None
    agent_name: Optional[str] = Field(None, max_length=20)
    email_signature: Optional[str] = Field(None, max_length=500)


class ConnectShopifyRequest(BaseModel):
    shop_domain: str = Field(..., min_length=3)
    access_token: str = Field(..., min_length=10)


class TestReplyRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)


class ExcludedIdsRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)


class CreateEmailAutomationRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    trigger: str = Field(..., pattern="^(cancel_order|refund|exchange|change_address)$")
    subject: str = Field(..., min_length=1, max_length=255)
    body: str = Field(..., min_length=1)
    enabled: bool = False
    requires_approval: bool = True


class UpdateEmailAutomationRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    subject: Optional[str] = Field(None, min_length=1, max_length=255)
    body: Optional[str] = Field(None, min_length=1)
    enabled: Optional[bool] = None
    requires_approval: Optional[bool] = None


class UpdateReplyStyleRequest(BaseModel):
    mode: Optional[str] = Field(None, pattern="^(preset|learned|disabled)$")
    preset: Optional[str] = None
    learn_automatically: Optional[bool] = None
    use_uploaded_only: Optional[bool] = None


class AddReplyExampleRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)


# ==================== Routes ====================

@router.get("")
async def list_brands(
    tenant: TenantContext = Depends(get_current_tenant),
    active_only: bool = Query(True),
):
    """List brands owned by the current tenant."""
    try:
        params: dict = {}
        if active_only:
            params["is_active"] = "is.true"

        owned = supabase_select("brands", {"tenant_id": f"eq.{tenant.tenant_id}", **params})

        if not owned:
            from src.services.auth_service import auth_service
            tenant_data = await auth_service.get_tenant(tenant.tenant_id)
            shopify_domain = (tenant_data or {}).get("shopify_domain")
            if shopify_domain:
                owned = supabase_select("brands", {"shopify_domain": f"eq.{shopify_domain}", **params})

        return {"brands": [_strip_secrets(b) for b in owned], "count": len(owned)}
    except Exception as e:
        logger.error(f"Error listing brands: {e}")
        raise HTTPException(status_code=500, detail="Failed to list brands")


@router.post("")
async def create_brand(
    request: CreateBrandRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Create a new brand, owned by the current tenant."""
    try:
        from src.services.plan_service import check_limit, build_limit_error
        brand_limit = check_limit(tenant.tenant_id, "brands", email=tenant.email)
        if not brand_limit["allowed"]:
            raise HTTPException(status_code=402, detail=build_limit_error("brands", brand_limit))

        brand_data: dict = {
            "name": request.name,
            "is_active": True,
            "tenant_id": tenant.tenant_id,
        }
        if request.support_email:
            brand_data["support_email"] = request.support_email
        if request.shopify_shop_name:
            brand_data["shopify_shop_name"] = request.shopify_shop_name
        if request.shopify_domain:
            brand_data["shopify_domain"] = request.shopify_domain

        result = supabase_insert("brands", brand_data)
        logger.info(f"[v2/brands] Created brand '{request.name}' for tenant {tenant.tenant_id}")
        return {"success": True, "brand": _strip_secrets(result) if result else brand_data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating brand: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{brand_id}")
async def get_brand(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Get a specific brand (must belong to current tenant)."""
    try:
        brand = _get_owned_brand(brand_id, tenant.tenant_id)
        return {"brand": _strip_secrets(brand)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting brand: {e}")
        raise HTTPException(status_code=500, detail="Failed to get brand")


@router.get("/{brand_id}/feedback")
async def list_brand_feedback(
    brand_id: str,
    rating: Optional[str] = None,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Customer feedback for this brand — powers the dashboard's Customer
    Voice view and the testimonials/trust section (rating=positive filter,
    real comments only, never fabricated). Returns the most recent 50 rows
    for display plus a `summary` (average rating, total, 1-5 breakdown)
    computed over ALL of the brand's feedback, not just the page shown —
    pure aggregation over real rows, omitted entirely when there's none."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        params = {
            "brand_id": f"eq.{brand_id}",
            "order": "created_at.desc",
            "limit": "1000",
        }
        if rating:
            params["rating"] = f"eq.{rating}"
        all_feedback = supabase_select("chat_feedback", params) or []

        # Attach each row's ticket channel (chat vs email) — one batch
        # lookup, not N+1 queries.
        ticket_ids = list({f["ticket_id"] for f in all_feedback if f.get("ticket_id")})
        channel_by_ticket = {}
        if ticket_ids:
            id_list = ",".join(ticket_ids)
            tickets = supabase_select("tickets", {"id": f"in.({id_list})"}) or []
            channel_by_ticket = {t["id"]: t.get("channel") for t in tickets}
        for f in all_feedback:
            f["channel"] = channel_by_ticket.get(f.get("ticket_id"), "unknown")

        starred = [f["rating_stars"] for f in all_feedback if f.get("rating_stars")]
        summary = None
        if starred:
            breakdown = {n: 0 for n in range(1, 6)}
            for s in starred:
                if 1 <= s <= 5:
                    breakdown[s] += 1
            summary = {
                "average": round(sum(starred) / len(starred), 1),
                "total": len(starred),
                "breakdown": breakdown,
            }

        return {"feedback": all_feedback[:50], "summary": summary}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing brand feedback: {e}")
        raise HTTPException(status_code=500, detail="Failed to list feedback")


_ANALYTICS_WINDOW_DAYS = 30
# Below this many ever-executed cancel_order actions, an "approval rate" is
# too noisy to call a readiness signal (e.g. 1/1 = 100% is not evidence of
# anything) — the card is omitted entirely rather than shown misleadingly.
_AUTOPILOT_MIN_SAMPLE = 5

# Action types that could eventually surface an Autopilot Readiness card.
# Only "cancel_order" is actually computed today (see get_brand_analytics) —
# listed here so a future category only needs a line added, not a new
# framework; deliberately not built out further than that yet.
_READINESS_CATEGORIES = {"cancel_order": "cancellation"}


def _category_readiness_status(total: int, failed: int, min_sample: int) -> str:
    """Never an invented threshold beyond the existing _AUTOPILOT_MIN_SAMPLE
    gate: below it there simply isn't enough of a track record yet
    ("not_ready"). At or above it, a real Shopify execution failure in the
    sample (as opposed to a human rejection, which is normal, expected
    judgment) is treated as something to review before recommending
    automation ("almost_there") rather than folded silently into a passing
    percentage. No failures at or above the sample floor is "ready_for_review"."""
    if total < min_sample:
        return "not_ready"
    if failed > 0:
        return "almost_there"
    return "ready_for_review"


def _categorize_failure_reason(reason: Optional[str]) -> str:
    """Buckets a real, already-stored error_message into a short label a
    merchant can scan at a glance. Never invents a reason - only labels
    the real one already on the action record."""
    text = (reason or "").lower()
    if any(k in text for k in ("access token", "not connected", "store connected", "reconnect", "unauthorized", "scope")):
        return "Connection issue"
    if any(k in text for k in ("already been", "already cancelled", "already refunded", "already executed", "no longer")):
        return "Order state changed"
    return "Shopify error"


def _compute_cancellation_readiness(brand_id: str) -> dict:
    """The single source of truth for cancellation Autopilot readiness —
    called both by GET /analytics (for the Automation page) and by the
    /automation/cancellation/enable endpoint (to independently re-verify
    readiness server-side at activation time, never trusting whatever the
    frontend last rendered). Extracted from the inline computation that
    used to live only in get_brand_analytics, with no change in behavior.

    Also includes real Cancellation Autopilot execution stats:
    approved_by="autopilot" is set nowhere else in the codebase (only
    _maybe_autopilot_cancel in return_actions_integration.py sets it, via
    the same actions_service.approve_action() a human's Approve click
    calls), so counting by that field can never include an ordinary
    human-approved Copilot action."""
    all_cancel_actions = supabase_select("actions", {
        "brand_id": f"eq.{brand_id}",
        "action_type": "eq.cancel_order",
    }) or []
    cancel_executed = sum(1 for a in all_cancel_actions if a.get("status") == "executed")
    cancel_rejected = sum(1 for a in all_cancel_actions if a.get("status") == "rejected")
    # A genuine Shopify execution failure (human approved, but the
    # Shopify call itself failed - e.g. a missing scope, order state
    # changed mid-flight) is a real signal about whether this category
    # is ready for unattended execution, distinct from a human rejection.
    # It counts against the rate (same as a rejection would) and toward
    # the minimum sample size.
    cancel_failed = sum(1 for a in all_cancel_actions if a.get("status") == "failed")
    cancel_sample = cancel_executed + cancel_rejected + cancel_failed

    # All genuine execution failures (human-approved or autopilot), most
    # recent first - so the readiness card's "N execution failures" claim
    # is never just a number with nothing behind it. Never excluded from
    # the readiness math above; this is purely additional visibility.
    all_failed = [a for a in all_cancel_actions if a.get("status") == "failed"]
    recent_failures = [
        {
            "order_id": a.get("order_id"),
            "reason": a.get("error_message") or "Cancellation could not be completed.",
            "category": _categorize_failure_reason(a.get("error_message")),
            "occurred_at": a.get("updated_at"),
            "status": "failed",
        }
        for a in sorted(all_failed, key=lambda a: a.get("updated_at") or "", reverse=True)[:5]
    ]

    # Autopilot's own execution track record - a strict subset of the
    # actions above (every autopilot-approved action is also one of the
    # cancel_order actions counted above; this never double-counts).
    autopilot_actions = [a for a in all_cancel_actions if a.get("approved_by") == "autopilot"]
    autopilot_handled = len(autopilot_actions)
    autopilot_successful = sum(1 for a in autopilot_actions if a.get("status") == "executed")
    # "Escalated for review" here is specifically an automatic attempt
    # that Shopify itself rejected/failed (actions_service._mark_failed
    # already recorded the real reason in error_message) - a genuine
    # execution failure, not a human rejection. A pre-execution decline
    # (order fulfilled, or a merchant policy requiring human judgment)
    # never reaches an autopilot attempt at all - it's staged as an
    # ordinary pending Copilot action instead, visible in the existing
    # Escalations queue exactly as it always has been.
    autopilot_failed = [a for a in autopilot_actions if a.get("status") == "failed"]
    recent_escalations = [
        {
            "order_id": a.get("order_id"),
            "reason": a.get("error_message") or "Automatic cancellation could not be completed.",
            "escalated_at": a.get("updated_at"),
        }
        for a in sorted(autopilot_failed, key=lambda a: a.get("updated_at") or "", reverse=True)[:5]
    ]

    return {
        "category": "cancellation",
        "total_requests": cancel_sample,
        "successful": cancel_executed,
        "escalated": cancel_rejected,
        "failed_executions": cancel_failed,
        "approval_rate": round(100 * cancel_executed / cancel_sample, 1) if cancel_sample > 0 else None,
        "status": _category_readiness_status(cancel_sample, cancel_failed, _AUTOPILOT_MIN_SAMPLE),
        "min_sample": _AUTOPILOT_MIN_SAMPLE,
        "recent_failures": recent_failures,
        "autopilot": {
            "handled_automatically": autopilot_handled,
            "successful": autopilot_successful,
            "escalated_for_review": len(autopilot_failed),
            "success_rate": round(100 * autopilot_successful / autopilot_handled, 1) if autopilot_handled > 0 else None,
            "recent_escalations": recent_escalations,
        },
    }


def _compute_refund_readiness(brand_id: str) -> dict:
    """Refund Autopilot readiness — same category-readiness shape and
    thresholds as _compute_cancellation_readiness (reuses
    _category_readiness_status and _AUTOPILOT_MIN_SAMPLE, never a second
    analytics system), but a deliberately separate function and a
    deliberately separate `actions` query filtered to action_type=refund,
    so nothing here can ever touch Cancellation Autopilot's own
    computation or data. approved_by="autopilot" is set on a refund action
    only by _maybe_autopilot_refund in return_actions_integration.py, via
    the same actions_service.approve_action() human approval calls."""
    all_refund_actions = supabase_select("actions", {
        "brand_id": f"eq.{brand_id}",
        "action_type": "eq.refund",
    }) or []
    refund_executed = sum(1 for a in all_refund_actions if a.get("status") == "executed")
    refund_rejected = sum(1 for a in all_refund_actions if a.get("status") == "rejected")
    refund_failed = sum(1 for a in all_refund_actions if a.get("status") == "failed")
    refund_sample = refund_executed + refund_rejected + refund_failed

    autopilot_actions = [a for a in all_refund_actions if a.get("approved_by") == "autopilot"]
    autopilot_handled = len(autopilot_actions)
    autopilot_successful = sum(1 for a in autopilot_actions if a.get("status") == "executed")
    autopilot_failed = [a for a in autopilot_actions if a.get("status") == "failed"]
    recent_escalations = [
        {
            "order_id": a.get("order_id"),
            "reason": a.get("error_message") or "Automatic refund could not be completed.",
            "escalated_at": a.get("updated_at"),
        }
        for a in sorted(autopilot_failed, key=lambda a: a.get("updated_at") or "", reverse=True)[:5]
    ]

    return {
        "category": "refund",
        "total_requests": refund_sample,
        "successful": refund_executed,
        "escalated": refund_rejected,
        "failed_executions": refund_failed,
        "approval_rate": round(100 * refund_executed / refund_sample, 1) if refund_sample > 0 else None,
        "status": _category_readiness_status(refund_sample, refund_failed, _AUTOPILOT_MIN_SAMPLE),
        "min_sample": _AUTOPILOT_MIN_SAMPLE,
        "autopilot": {
            "handled_automatically": autopilot_handled,
            "successful": autopilot_successful,
            "escalated_for_review": len(autopilot_failed),
            "success_rate": round(100 * autopilot_successful / autopilot_handled, 1) if autopilot_handled > 0 else None,
            "recent_escalations": recent_escalations,
        },
    }


def _compute_generic_category_readiness(brand_id: str, action_type: str, category_label: str) -> dict:
    """Read-only readiness view for a category with no Autopilot execution
    capability today (Exchanges, Address changes) — same real metrics and
    the same _category_readiness_status/_AUTOPILOT_MIN_SAMPLE thresholds as
    Cancellation/Refund above, deliberately without an "autopilot" stats
    sub-object or an enable/disable endpoint: no _maybe_autopilot_* hook
    exists for these action types, so nothing computed here can ever be
    turned on. Exists so the Training/AI Readiness page can show a category
    its real track record instead of a static "Coming soon" with no numbers
    behind it, without adding any new automation capability."""
    actions = supabase_select("actions", {
        "brand_id": f"eq.{brand_id}",
        "action_type": f"eq.{action_type}",
    }) or []
    executed = sum(1 for a in actions if a.get("status") == "executed")
    rejected = sum(1 for a in actions if a.get("status") == "rejected")
    failed = sum(1 for a in actions if a.get("status") == "failed")
    sample = executed + rejected + failed
    return {
        "category": category_label,
        "total_requests": sample,
        "successful": executed,
        "escalated": rejected,
        "failed_executions": failed,
        "approval_rate": round(100 * executed / sample, 1) if sample > 0 else None,
        "status": _category_readiness_status(sample, failed, _AUTOPILOT_MIN_SAMPLE),
        "min_sample": _AUTOPILOT_MIN_SAMPLE,
    }


@router.get("/{brand_id}/training-readiness")
async def get_training_readiness(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Training / AI Readiness — the merchant-facing summary of "what has
    Luna learned, what have humans verified, what's ready to automate".
    Deliberately a pure read-only composition of data that already exists
    for its own settings page or endpoint elsewhere (Reply Style, Knowledge
    Base, ticket review outcomes via tickets._compute_review_status,
    Cancellation/Refund Autopilot readiness) — no new learning system, no
    new thresholds, no invented metrics. Brand-level counts only; never
    returns a customer name, email, or message body."""
    try:
        brand = _get_owned_brand(brand_id, tenant.tenant_id)
        from src.api.routes.tickets import _compute_review_status, _ticket_has_luna_reply

        # ---- Train: what the merchant has taught Luna ----
        kb_sources = supabase_select("knowledge_base_sources", {"brand_id": f"eq.{brand_id}"}) or []
        kb_completed = sum(1 for s in kb_sources if s.get("status") == "completed")
        examples = supabase_select("reply_style_examples", {"brand_id": f"eq.{brand_id}"}) or []
        approved_count = reply_style_service.count_eligible_approved_replies(brand_id)
        reply_style_mode = brand.get("reply_style_mode") or "preset"
        reply_style_learned = reply_style_mode == "learned" and bool(brand.get("reply_style_profile"))
        has_policies = bool(
            brand.get("return_policy_days") or brand.get("refund_notes") or brand.get("final_sale_tags")
        )

        train = {
            "knowledge": {
                "sources_count": len(kb_sources),
                "completed_count": kb_completed,
                "has_any": kb_completed > 0,
            },
            "policies": {"has_any": has_policies, "return_policy_days": brand.get("return_policy_days")},
            "examples": {"count": len(examples)},
            "reply_style": {
                "mode": reply_style_mode,
                "learned": reply_style_learned,
                "approved_reply_count": approved_count,
                "min_replies_required": reply_style_service.MIN_APPROVED_REPLIES_TO_LEARN,
            },
        }

        # ---- Verify: real human review outcomes over Luna's replies ----
        all_tickets = supabase_select("tickets", {"brand_id": f"eq.{brand_id}"}) or []
        reviewable_statuses = [_compute_review_status(t) for t in all_tickets]
        reviewable_statuses = [s for s in reviewable_statuses if s is not None]
        needs_review = sum(1 for s in reviewable_statuses if s == "needs_review")
        approved_n = sum(1 for s in reviewable_statuses if s == "approved")
        edited_n = sum(1 for s in reviewable_statuses if s == "edited")
        rejected_n = sum(1 for s in reviewable_statuses if s == "rejected")
        reviewed_total = approved_n + edited_n + rejected_n

        # total_ai_conversations must count every ticket Luna actually
        # replied to, not just the subset _compute_review_status() finds
        # via the scalar ai_reply/ai_draft/ai_response columns — an
        # auto-resolved chat-widget conversation's reply lives only in
        # `messages` (see _ticket_has_luna_reply). conversations_reviewed/
        # needing_review/the rates above are deliberately untouched: they
        # answer "of the conversations that needed a human decision, what
        # happened", which is a real, different question from "how many
        # conversations did Luna handle at all".
        total_ai_conversations = sum(1 for t in all_tickets if _ticket_has_luna_reply(t))

        feedback = supabase_select("chat_feedback", {"brand_id": f"eq.{brand_id}"}) or []
        starred = [f["rating_stars"] for f in feedback if f.get("rating_stars")]
        csat = {"average": round(sum(starred) / len(starred), 1), "total": len(starred)} if starred else None

        verify = {
            "total_ai_conversations": total_ai_conversations,
            "conversations_reviewed": reviewed_total,
            "conversations_needing_review": needs_review,
            "approval_rate": round(100 * (approved_n + edited_n) / reviewed_total, 1) if reviewed_total else None,
            "edit_rate": round(100 * edited_n / reviewed_total, 1) if reviewed_total else None,
            "rejection_rate": round(100 * rejected_n / reviewed_total, 1) if reviewed_total else None,
            "csat": csat,
        }

        # ---- Automate: category-specific readiness, reusing the exact
        # same computation the Automation page's /analytics already uses ----
        cancellation_autopilot_enabled = bool(brand.get("cancellation_autopilot_enabled"))
        refund_autopilot_enabled = bool(brand.get("refund_autopilot_enabled"))
        automate = {
            "cancellation": {
                **_compute_cancellation_readiness(brand_id),
                "mode": "autopilot" if cancellation_autopilot_enabled else "copilot",
                "enabled": cancellation_autopilot_enabled,
                "autopilot_capable": True,
            },
            "refund": {
                **_compute_refund_readiness(brand_id),
                "mode": "autopilot" if refund_autopilot_enabled else "copilot",
                "enabled": refund_autopilot_enabled,
                "autopilot_capable": True,
            },
            "exchange": {
                **_compute_generic_category_readiness(brand_id, "exchange", "Exchanges"),
                "mode": "copilot",
                "enabled": False,
                "autopilot_capable": False,
            },
            "address_change": {
                **_compute_generic_category_readiness(brand_id, "change_address", "Address changes"),
                "mode": "copilot",
                "enabled": False,
                "autopilot_capable": False,
            },
        }

        return {"train": train, "verify": verify, "automate": automate}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error computing training readiness: {e}")
        raise HTTPException(status_code=500, detail="Failed to compute training readiness")


@router.get("/{brand_id}/analytics")
async def get_brand_analytics(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Resolution analytics + Autopilot readiness — every number here is a
    real aggregation over this brand's own tickets/actions/feedback in the
    last 30 days (autopilot readiness looks at all-time executed actions,
    a track record, not a recent window). Nothing is estimated or
    invented; a metric that can't be reliably computed from current data
    (e.g. response time when no ticket has first_response_at set) is
    omitted rather than guessed."""
    try:
        brand = _get_owned_brand(brand_id, tenant.tenant_id)
        window_start = (datetime.now(timezone.utc) - timedelta(days=_ANALYTICS_WINDOW_DAYS)).isoformat()

        tickets = supabase_select("tickets", {
            "store_id": f"eq.{brand_id}",
            "created_at": f"gte.{window_start}",
        }) or []
        conversations_handled = len(tickets)
        resolved_by_luna = sum(1 for t in tickets if t.get("status") == "auto_resolved")
        escalated_to_human = sum(1 for t in tickets if t.get("status") == "escalated")

        response_times = []
        for t in tickets:
            if t.get("channel") == "email" and t.get("first_response_at") and t.get("created_at"):
                try:
                    created = datetime.fromisoformat(t["created_at"].replace("Z", "+00:00"))
                    responded = datetime.fromisoformat(t["first_response_at"].replace("Z", "+00:00"))
                    response_times.append((responded - created).total_seconds())
                except (ValueError, AttributeError):
                    pass
        avg_response_time_seconds = round(sum(response_times) / len(response_times)) if response_times else None

        actions = supabase_select("actions", {
            "brand_id": f"eq.{brand_id}",
            "created_at": f"gte.{window_start}",
        }) or []
        executed = sum(1 for a in actions if a.get("status") == "executed")
        rejected = sum(1 for a in actions if a.get("status") == "rejected")
        approval_rate = round(100 * executed / (executed + rejected), 1) if (executed + rejected) > 0 else None
        cancellation_count = sum(1 for a in actions if a.get("action_type") == "cancel_order")
        refund_count = sum(1 for a in actions if a.get("action_type") == "refund")

        feedback = supabase_select("chat_feedback", {"brand_id": f"eq.{brand_id}", "created_at": f"gte.{window_start}"}) or []
        starred = [f["rating_stars"] for f in feedback if f.get("rating_stars")]
        csat = round(sum(starred) / len(starred), 1) if starred else None

        # Autopilot readiness: all-time track record for cancel_order,
        # independent of the 30-day analytics window above. Reuses
        # _compute_cancellation_readiness (the same helper the enable
        # endpoint independently re-verifies against) rather than a second,
        # possibly-drifting computation.
        cancellation_readiness = _compute_cancellation_readiness(brand_id)
        cancel_sample = cancellation_readiness["total_requests"]
        autopilot_readiness = None
        if cancel_sample >= _AUTOPILOT_MIN_SAMPLE:
            autopilot_readiness = {
                "eligible_cancellations": cancellation_readiness["successful"],
                "failed_executions": cancellation_readiness["failed_executions"],
                "approval_rate": cancellation_readiness["approval_rate"],
            }

        # Per-category readiness detail for the Automation page (Phase 1/4).
        # Unlike autopilot_readiness (only present once the minimum sample
        # is met, kept as-is for the existing Customer Voice card), this is
        # always present once the brand has a Shopify connection, since
        # "why isn't this ready yet" needs the real numbers even below the
        # sample floor. cancellation_autopilot_enabled reads safely as
        # falsy even before migration 047 is applied (see that file).
        cancellation_autopilot_enabled = bool(brand.get("cancellation_autopilot_enabled"))
        refund_autopilot_enabled = bool(brand.get("refund_autopilot_enabled"))
        refund_readiness = _compute_refund_readiness(brand_id)
        category_readiness = {
            "cancellation": {
                **cancellation_readiness,
                "mode": "autopilot" if cancellation_autopilot_enabled else "copilot",
                "enabled": cancellation_autopilot_enabled,
            },
            "refund": {
                **refund_readiness,
                "mode": "autopilot" if refund_autopilot_enabled else "copilot",
                "enabled": refund_autopilot_enabled,
            },
        }

        return {
            "window_days": _ANALYTICS_WINDOW_DAYS,
            "conversations_handled": conversations_handled,
            "resolved_by_luna": resolved_by_luna,
            "escalated_to_human": escalated_to_human,
            "avg_response_time_seconds": avg_response_time_seconds,
            "approval_rate": approval_rate,
            "cancellation_count": cancellation_count,
            "refund_count": refund_count,
            "csat": csat,
            "autopilot_readiness": autopilot_readiness,
            "category_readiness": category_readiness,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error computing brand analytics: {e}")
        raise HTTPException(status_code=500, detail="Failed to compute analytics")


@router.post("/{brand_id}/automation/cancellation/enable")
async def enable_cancellation_autopilot(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Dedicated, authenticated activation endpoint for Cancellation
    Autopilot — the ONLY way this flag can be turned on (never a generic
    settings PATCH, never a value trusted from the request body). Every
    condition is re-verified here from data the merchant cannot control
    from the browser:
    - tenant authentication + brand ownership: _get_owned_brand (same
      pattern every brand-scoped endpoint in this file already uses; 404s
      rather than 403s for a brand owned by someone else).
    - the merchant owns a real, connected Shopify store.
    - entitlement: the existing plan_service.check_limit("shopify_actions")
      primitive already used to gate real Shopify-executing actions
      elsewhere (v2_tickets.py).
    - readiness: a fresh server-side recomputation via
      _compute_cancellation_readiness — never the readiness object the
      frontend happened to last render."""
    try:
        brand = _get_owned_brand(brand_id, tenant.tenant_id)

        if not brand.get("shopify_connected"):
            raise HTTPException(
                status_code=400,
                detail="Connect a Shopify store before enabling Cancellation Autopilot.",
            )

        from src.services.plan_service import check_limit, build_limit_error
        entitlement = check_limit(tenant.tenant_id, "shopify_actions", email=tenant.email)
        if not entitlement["allowed"]:
            raise HTTPException(status_code=402, detail=build_limit_error("shopify_actions", entitlement))

        readiness = _compute_cancellation_readiness(brand_id)
        if readiness["status"] != "ready_for_review":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cancellation Autopilot isn't ready to enable yet "
                    f"(status: {readiness['status']}). It needs at least {readiness['min_sample']} "
                    "real cancellation outcomes with no unresolved Shopify execution failures."
                ),
            )

        supabase_update("brands", {"id": f"eq.{brand_id}"}, {
            "cancellation_autopilot_enabled": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"[Automation] Cancellation Autopilot ENABLED for brand {brand_id} (tenant {tenant.tenant_id})")
        return {"success": True, "enabled": True, "readiness": readiness}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error enabling cancellation autopilot: {e}")
        raise HTTPException(status_code=500, detail="Failed to enable Cancellation Autopilot")


@router.post("/{brand_id}/automation/cancellation/disable")
async def disable_cancellation_autopilot(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Kill switch. Only auth + ownership is required — a merchant must
    always be able to turn this off immediately, with no readiness gate.
    Flipping this flag only stops NEW automatic cancellations from the
    next request onward (return_actions_integration.py reads it fresh on
    every request); it cannot touch or corrupt an action that has already
    atomically claimed "approved" and is mid-flight against Shopify — same
    protection every action in this system already has via
    actions_service.approve_action()'s conditional claim."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        supabase_update("brands", {"id": f"eq.{brand_id}"}, {
            "cancellation_autopilot_enabled": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"[Automation] Cancellation Autopilot DISABLED for brand {brand_id} (tenant {tenant.tenant_id})")
        return {"success": True, "enabled": False}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error disabling cancellation autopilot: {e}")
        raise HTTPException(status_code=500, detail="Failed to disable Cancellation Autopilot")


@router.post("/{brand_id}/automation/refund/enable")
async def enable_refund_autopilot(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Dedicated, authenticated activation endpoint for Refund Autopilot —
    a separate flag from Cancellation Autopilot, gated independently.
    Mirrors enable_cancellation_autopilot's verification exactly (auth +
    ownership, connected Shopify store, entitlement via the existing
    plan_service.check_limit("shopify_actions"), a fresh server-side
    readiness recomputation), never trusting a frontend toggle. Refunds
    are financially sensitive, so the actual automatic-execution safety
    gates (deterministic full-refund-only amount, no ambiguous partial
    figure) live in _maybe_autopilot_refund — this endpoint only controls
    whether that path is reachable at all."""
    try:
        brand = _get_owned_brand(brand_id, tenant.tenant_id)

        if not brand.get("shopify_connected"):
            raise HTTPException(
                status_code=400,
                detail="Connect a Shopify store before enabling Refund Autopilot.",
            )

        from src.services.plan_service import check_limit, build_limit_error
        entitlement = check_limit(tenant.tenant_id, "shopify_actions", email=tenant.email)
        if not entitlement["allowed"]:
            raise HTTPException(status_code=402, detail=build_limit_error("shopify_actions", entitlement))

        readiness = _compute_refund_readiness(brand_id)
        if readiness["status"] != "ready_for_review":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Refund Autopilot isn't ready to enable yet "
                    f"(status: {readiness['status']}). It needs at least {readiness['min_sample']} "
                    "real refund outcomes with no unresolved Shopify execution failures."
                ),
            )

        supabase_update("brands", {"id": f"eq.{brand_id}"}, {
            "refund_autopilot_enabled": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"[Automation] Refund Autopilot ENABLED for brand {brand_id} (tenant {tenant.tenant_id})")
        return {"success": True, "enabled": True, "readiness": readiness}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error enabling refund autopilot: {e}")
        raise HTTPException(status_code=500, detail="Failed to enable Refund Autopilot")


@router.post("/{brand_id}/automation/refund/disable")
async def disable_refund_autopilot(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Kill switch. Only auth + ownership is required — no readiness gate,
    same as cancellation's disable endpoint. Stops NEW automatic refunds
    from the next request onward; cannot touch an action already
    atomically claimed "approved" and mid-flight against Shopify."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        supabase_update("brands", {"id": f"eq.{brand_id}"}, {
            "refund_autopilot_enabled": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"[Automation] Refund Autopilot DISABLED for brand {brand_id} (tenant {tenant.tenant_id})")
        return {"success": True, "enabled": False}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error disabling refund autopilot: {e}")
        raise HTTPException(status_code=500, detail="Failed to disable Refund Autopilot")


@router.patch("/{brand_id}")
async def update_brand(
    brand_id: str,
    request: UpdateBrandRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Update brand settings."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        updates = request.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = supabase_update("brands", {"id": f"eq.{brand_id}"}, updates)
        return {"success": True, "brand": _strip_secrets(result) if result else None}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating brand: {e}")
        raise HTTPException(status_code=500, detail="Failed to update brand")


@router.get("/{brand_id}/refund-policy/excluded-products")
async def list_excluded_products(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """List Shopify product IDs excluded from refund eligibility for this brand."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        rows = supabase_select("refund_policy_excluded_products", {"brand_id": f"eq.{brand_id}"})
        return {"ids": [r["shopify_product_id"] for r in rows]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing excluded products: {e}")
        raise HTTPException(status_code=500, detail="Failed to list excluded products")


@router.put("/{brand_id}/refund-policy/excluded-products")
async def replace_excluded_products(
    brand_id: str,
    request: ExcludedIdsRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Replace the full list of Shopify product IDs excluded from refund eligibility.
    Runs atomically via a Postgres function (see migration 024) so a failure
    partway through can never leave the list half-written."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        result = supabase_rpc("replace_refund_policy_excluded_products", {
            "p_brand_id": brand_id,
            "p_product_ids": request.ids,
        })
        if result is not True:
            raise HTTPException(status_code=500, detail="Failed to update excluded products")
        return {"success": True, "ids": request.ids}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating excluded products: {e}")
        raise HTTPException(status_code=500, detail="Failed to update excluded products")


@router.get("/{brand_id}/refund-policy/excluded-collections")
async def list_excluded_collections(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """List Shopify collection IDs excluded from refund eligibility for this brand."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        rows = supabase_select("refund_policy_excluded_collections", {"brand_id": f"eq.{brand_id}"})
        return {"ids": [r["shopify_collection_id"] for r in rows]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing excluded collections: {e}")
        raise HTTPException(status_code=500, detail="Failed to list excluded collections")


@router.put("/{brand_id}/refund-policy/excluded-collections")
async def replace_excluded_collections(
    brand_id: str,
    request: ExcludedIdsRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Replace the full list of Shopify collection IDs excluded from refund eligibility.
    Runs atomically via a Postgres function (see migration 024) so a failure
    partway through can never leave the list half-written."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        result = supabase_rpc("replace_refund_policy_excluded_collections", {
            "p_brand_id": brand_id,
            "p_collection_ids": request.ids,
        })
        if result is not True:
            raise HTTPException(status_code=500, detail="Failed to update excluded collections")
        return {"success": True, "ids": request.ids}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating excluded collections: {e}")
        raise HTTPException(status_code=500, detail="Failed to update excluded collections")


@router.delete("/{brand_id}")
async def delete_brand(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Soft-delete a brand (marks inactive)."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        supabase_update("brands", {"id": f"eq.{brand_id}"}, {"is_active": False})
        return {"success": True, "message": "Brand deactivated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting brand: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete brand")


async def _connect_shopify_credentials(brand_id: str, tenant_id: str, shop_domain: str, access_token: str) -> dict:
    """Core of connecting a Shopify store to a brand — shared by the manual
    access-token endpoint below and the OAuth callback (shopify_auth.py),
    so the domain-conflict-claim logic only exists once. Never raises
    HTTPException (callers translate the returned shape appropriately —
    one into an HTTP error response, the other into a redirect).

    If another brand already owns this domain (unique constraint), we claim
    that brand for this tenant and deactivate the newly-created placeholder
    brand."""
    from src.services.shopify_service import ShopifyClient

    # ShopifyClient._normalize_domain() strips a pasted http(s):// scheme,
    # trailing slash, and appends .myshopify.com — do that once here and
    # reuse client_shopify.shop_domain everywhere below, instead of a
    # second hand-rolled normalization that didn't strip the scheme and
    # let "https://store.myshopify.com" get stored verbatim.
    client_shopify = ShopifyClient(shop_domain, access_token)
    shop_domain = client_shopify.shop_domain
    validation = await client_shopify.validate_connection()

    if not validation.get("success"):
        return {"success": False, "status_code": 400, "error": validation.get("error", "Failed to connect to Shopify"), "error_code": "connection_failed"}

    shopify_fields = {
        "shopify_domain": shop_domain,
        "shopify_access_token": encrypt_token(access_token),
        "shopify_shop_name": validation.get("shop_name"),
        "shopify_connected": True,
        "tenant_id": tenant_id,
    }

    active_brand_id = brand_id
    try:
        supabase_update("brands", {"id": f"eq.{brand_id}"}, shopify_fields)
    except Exception as upd_err:
        err_str = str(upd_err)
        if "409" in err_str or "23505" in err_str or "conflict" in err_str.lower():
            # Domain unique constraint — the domain is already connected to some
            # brand. Only claim it if that brand has no owner yet (a genuinely
            # unclaimed placeholder). If it already belongs to a different,
            # real tenant, claiming it would silently hijack that tenant's
            # brand (their Shopify connection, Gmail connection, tickets, the
            # works) onto this caller's account - confirmed as a real incident
            # during testing, not a hypothetical. Reject instead.
            existing = supabase_select("brands", {"shopify_domain": f"eq.{shop_domain}"})
            if not existing:
                raise
            existing_brand = existing[0]
            existing_tenant_id = existing_brand.get("tenant_id")
            if existing_tenant_id and existing_tenant_id != tenant_id:
                logger.warning(
                    f"[v2/brands] Rejected Shopify connect: domain {shop_domain} already "
                    f"belongs to tenant {existing_tenant_id}, not requesting tenant {tenant_id}"
                )
                return {"success": False, "status_code": 409, "error": "This Shopify store is already connected to a different tResolv account.", "error_code": "domain_taken"}
            active_brand_id = existing_brand["id"]
            supabase_update("brands", {"id": f"eq.{active_brand_id}"}, {
                "tenant_id": tenant_id,
                "shopify_access_token": encrypt_token(access_token),
                "shopify_connected": True,
                "is_active": True,
            })
            # Deactivate the empty placeholder that was just created
            if active_brand_id != brand_id:
                supabase_update("brands", {"id": f"eq.{brand_id}"}, {"is_active": False})
            logger.info(f"[v2/brands] Claimed unowned brand {active_brand_id} for tenant {tenant_id}")
        else:
            raise

    # Best-effort: record which scopes this token actually has, right
    # now, so onboarding/import can show a precise message instead of
    # discovering a missing permission mid-import.
    scope_result = await shopify_scope_service.check_and_store_scopes(active_brand_id, client_shopify)

    await supabase_service.log_onboarding_event(active_brand_id, "shopify_connected", {
        "shop_domain": shop_domain,
    })

    # Auto-start Shopify -> Knowledge Base ingestion right after a
    # successful connection, regardless of which surface initiated it
    # (onboarding, Settings, or the OAuth callback) - the merchant should
    # never have to separately remember to trigger the import that
    # already exists. Reuses the exact same idempotent, scope-gated
    # kickoff the explicit POST /shopify/import endpoint uses. Best-effort:
    # never fail a successful connection over an import that couldn't start.
    try:
        await _start_shopify_import_if_needed(
            active_brand_id,
            {"id": active_brand_id, "shopify_connected": True, "shopify_granted_scopes": scope_result.get("granted_scopes")},
            client=client_shopify,
        )
    except Exception as e:
        logger.warning(f"[v2/brands] Could not auto-start Shopify import for brand {active_brand_id}: {e}")

    return {
        "success": True,
        "shop_name": validation.get("shop_name"),
        "shop_domain": shop_domain,
        "brand_id": active_brand_id,  # May differ from URL brand_id after 409 resolution
        "client": client_shopify,  # reused by callers that want get_counts() without reconnecting
    }


async def _start_shopify_import_if_needed(brand_id: str, brand: dict, client=None, force: bool = False) -> dict:
    """Kick off background KB ingestion for a connected brand, unless it's
    already running or already completed - the exact logic the explicit
    POST /shopify/import endpoint used to inline. Pulled out so a
    successful Shopify connection (OAuth callback or manual token connect,
    from onboarding or Settings) can trigger the same ingestion
    automatically instead of requiring the merchant to separately visit
    onboarding's import step. Idempotent by construction: the
    knowledge_base_sources completed-check and the in-memory running-check
    below are the same guards the manual endpoint always had, so calling
    this on every (re)connect never duplicates an import."""
    if shopify_import_service.get_import_status(brand_id) == "running":
        return {"success": True, "status": "running"}

    # get_import_status() is an in-memory, single-process flag - it
    # resets to "not_started" on every restart/redeploy even though the
    # real knowledge (knowledge_base_sources rows + their rag_chunks)
    # is still there in the database. Without this check, onboarding
    # simply re-mounting this step after a restart would wipe and
    # re-fetch/re-embed the merchant's entire catalog for no reason
    # (see _clear_previous_import). knowledge_base_sources.status is
    # the real, persisted source of truth - reuse it instead of a
    # second one.
    if not force:
        already_imported = supabase_select("knowledge_base_sources", {
            "brand_id": f"eq.{brand_id}",
            "source_type": f"eq.{shopify_import_service.SOURCE_TYPE}",
            "status": "eq.completed",
            "limit": "1",
        })
        if already_imported:
            return {"success": True, "status": "done"}

    granted = brand.get("shopify_granted_scopes")
    if granted is None:
        # Brand connected before scope tracking existed - check live now
        # rather than starting an import that's blind to what will fail.
        shopify_client = client or shopify_import_service._get_client_for_brand(brand)
        if shopify_client:
            result = await shopify_scope_service.check_and_store_scopes(brand_id, shopify_client)
            granted = result.get("granted_scopes")
        granted = granted or []

    missing = shopify_scope_service.missing_scopes(granted, shopify_scope_service.IMPORT_SCOPES)
    if len(missing) == len(shopify_scope_service.IMPORT_SCOPES):
        # Neither read_products nor read_content is granted - every
        # resource the importer knows how to fetch would 403. Don't run
        # a doomed import; tell the merchant exactly what's missing.
        shopify_scope_service.set_blocked(brand_id, missing)
        return {
            "success": True,
            "status": "blocked_missing_scopes",
            "missing_scopes": missing,
            "message": "Your Shopify connection works, but additional permissions are required to import products and store content.",
            "reason": "These permissions allow tResolv to understand your products, policies, and store information so Luna can answer customers accurately.",
        }
    shopify_scope_service.clear_blocked(brand_id)

    await supabase_service.log_onboarding_event(brand_id, "shopify_import_started", {})
    asyncio.create_task(shopify_import_service.run_shopify_import(brand_id))
    return {"success": True, "status": "running"}


@router.get("/{brand_id}/shopify/oauth/start")
async def shopify_oauth_start(
    brand_id: str,
    shop: str = Query(..., description="Store domain, e.g. mybrand or mybrand.myshopify.com"),
    return_to: str = Query("onboarding", description="Dashboard page to return to after connecting: 'onboarding' or 'settings'"),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Authenticated: returns the Shopify OAuth authorization URL for this
    brand. The frontend navigates the browser to it directly (a full-page
    redirect can't carry the Authorization header, so the actual OAuth
    callback below has no auth dependency — the signed state proves which
    brand/tenant initiated it, same pattern as the Gmail OAuth flow)."""
    _get_owned_brand(brand_id, tenant.tenant_id)
    from src.services.shopify_oauth import get_authorize_url
    try:
        auth_url = get_authorize_url(brand_id, shop, return_to)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"auth_url": auth_url}


@router.post("/{brand_id}/shopify/connect")
async def connect_shopify(
    brand_id: str,
    request: ConnectShopifyRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Connect a Shopify store to a brand via a pasted Admin API access
    token (manual fallback — the primary path is the OAuth flow above)."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        result = await _connect_shopify_credentials(brand_id, tenant.tenant_id, request.shop_domain, request.access_token)
        if not result.get("success"):
            raise HTTPException(status_code=result.get("status_code", 400), detail=result.get("error"))
        return {
            "success": True,
            "shop_name": result.get("shop_name"),
            "shop_domain": result.get("shop_domain"),
            "brand_id": result.get("brand_id"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error connecting Shopify: {e}")
        raise HTTPException(status_code=500, detail="Failed to connect Shopify")


@router.post("/{brand_id}/shopify/import")
async def start_shopify_import(
    brand_id: str,
    force: bool = Query(False, description="Re-run the import even if it already completed - used by the Knowledge Base's 'Retry sync' action. Sources the merchant has manually edited are preserved (see _clear_previous_import)."),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Kick off the background import of products/policies/pages into the
    brand's knowledge base. Fire-and-forget - poll import-status for progress."""
    try:
        brand = _get_owned_brand(brand_id, tenant.tenant_id)
        if not brand.get("shopify_connected"):
            raise HTTPException(status_code=400, detail="Connect Shopify before importing.")
        return await _start_shopify_import_if_needed(brand_id, brand, force=force)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting Shopify import: {e}")
        raise HTTPException(status_code=500, detail="Failed to start import")


@router.get("/{brand_id}/shopify/import-status")
async def get_shopify_import_status(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Poll target for onboarding's import-progress screen."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        sources = supabase_select("knowledge_base_sources", {
            "brand_id": f"eq.{brand_id}",
            "source_type": f"eq.{shopify_import_service.SOURCE_TYPE}",
            "order": "created_at.asc",
        })
        blocked_scopes = shopify_scope_service.get_blocked(brand_id)
        in_memory_status = shopify_import_service.get_import_status(brand_id)
        has_completed = any(s.get("status") == "completed" for s in (sources or []))

        if blocked_scopes:
            status = "blocked_missing_scopes"
        elif in_memory_status == "running":
            status = "running"
        elif has_completed:
            # knowledge_base_sources is the real, persisted truth - report
            # "done" from it even if this process's in-memory status was
            # reset by a restart/redeploy and would otherwise say
            # "not_started" for knowledge that's actually already there.
            status = "done"
        else:
            status = in_memory_status  # "not_started" or "failed"

        missing_scopes = blocked_scopes if blocked_scopes else shopify_import_service.get_missing_scopes(brand_id)
        return {
            "status": status,
            # Single flag the rest of the app (Test Luna gating, Dashboard's
            # checklist) can read instead of each re-deriving "any source
            # completed and nothing currently running" itself.
            "ready": has_completed and status != "running",
            "missing_scopes": missing_scopes,
            "report": shopify_import_service.get_import_report(brand_id),
            "sources": [
                {
                    "name": s.get("name"),
                    "status": s.get("status"),
                    "chunk_count": s.get("chunk_count"),
                    "metadata": s.get("metadata"),
                }
                for s in (sources or [])
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting import status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get import status")


@router.get("/{brand_id}/shopify/health")
async def get_shopify_health(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Shopify connection health: which store/app is connected, what scopes
    that token actually has, and what's missing — the single place to answer
    'why isn't this working' without digging through logs.

    "status" is one of:
      - healthy                 - every required scope is granted
      - needs_permission_update - connection verified, a required scope is genuinely missing -> reconnect
      - check_unavailable       - connection recorded, but we couldn't reach Shopify to verify
                                   scopes right now (token invalid/revoked, or Shopify unreachable) -
                                   distinct from a confirmed missing scope, since reporting every
                                   check failure as "needs_permission_update" (every scope shown
                                   "missing") would be misleading when the real cause is transient
                                   or the token was revoked outright, not a narrower grant.
      - connection_unavailable  - shopify_connected=True but there's no usable client (missing
                                   domain/token, or the stored token failed to decrypt)
    Never a healthy/needs_permission_update verdict without an actual successful live scope check.
    """
    try:
        brand = _get_owned_brand(brand_id, tenant.tenant_id)
        if not brand.get("shopify_connected"):
            return {"connected": False, "status": "not_connected"}

        granted = brand.get("shopify_granted_scopes")
        app_name = brand.get("shopify_app_name")
        checked_at = brand.get("shopify_scopes_checked_at")
        check_error = None

        if granted is None:
            # Only need a live client - and only pay for a live Shopify call -
            # when there's no cached scope check to fall back on.
            client = shopify_import_service._get_client_for_brand(brand)
            if not client:
                return {
                    "connected": True,
                    "domain": brand.get("shopify_domain"),
                    "status": "connection_unavailable",
                    "granted_scopes": [],
                    "missing_scopes": [],
                    "missing_scope_labels": [],
                    "checked_at": None,
                }
            result = await shopify_scope_service.check_and_store_scopes(brand_id, client)
            granted, app_name, checked_at, check_error = (
                result.get("granted_scopes"), result.get("app_name"),
                result.get("checked_at"), result.get("error"),
            )

        if granted is None:
            # The live check ran and failed (or has never once succeeded) -
            # report that plainly instead of treating every required scope
            # as confirmed-missing.
            return {
                "connected": True,
                "domain": brand.get("shopify_domain"),
                "status": "check_unavailable",
                "check_error": check_error,
                "granted_scopes": [],
                "missing_scopes": [],
                "missing_scope_labels": [],
                "checked_at": checked_at,
            }

        missing = shopify_scope_service.missing_scopes(granted, list(shopify_scope_service.REQUIRED_SCOPES.keys()))

        return {
            "connected": True,
            "domain": brand.get("shopify_domain"),
            "app_name": app_name,
            "granted_scopes": granted,
            "missing_scopes": missing,
            "missing_scope_labels": [shopify_scope_service.REQUIRED_SCOPES.get(s, s) for s in missing],
            "status": "needs_permission_update" if missing else "healthy",
            "checked_at": checked_at,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting Shopify health for brand {brand_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get Shopify connection health")


@router.post("/{brand_id}/shopify/disconnect")
async def disconnect_shopify(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Disconnect Shopify from a brand."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        supabase_update("brands", {"id": f"eq.{brand_id}"}, {
            "shopify_domain": None,
            "shopify_access_token": None,
            "shopify_shop_name": None,
            "shopify_connected": False,
            "shopify_granted_scopes": None,
            "shopify_app_name": None,
            "shopify_scopes_checked_at": None,
        })
        shopify_scope_service.clear_blocked(brand_id)
        return {"success": True, "message": "Shopify disconnected"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error disconnecting Shopify: {e}")
        raise HTTPException(status_code=500, detail="Failed to disconnect Shopify")


# ==================== Reply Style ====================
# Wording/tone personalization — separate from Identity (agent_name,
# email_signature, both already covered by the generic PATCH above).
# Reply Style never affects facts, refund eligibility, or business logic.

@router.get("/{brand_id}/reply-style")
async def get_reply_style(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Current Reply Style state for the settings page: mode, active preset
    or learned profile, reasoning, learning controls, and the preset catalog."""
    try:
        brand = _get_owned_brand(brand_id, tenant.tenant_id)

        # Best-effort opportunistic regeneration check — never blocks the response.
        try:
            await reply_style_service.regenerate_if_due(brand_id)
            brand = _get_owned_brand(brand_id, tenant.tenant_id)
        except Exception as e:
            logger.warning(f"[ReplyStyle] regenerate_if_due check failed: {e}")

        active_style = reply_style_service.get_active_style(brand)
        approved_count = reply_style_service.count_eligible_approved_replies(brand_id)

        return {
            "mode": brand.get("reply_style_mode") or "preset",
            "preset": brand.get("reply_style_preset"),
            "learned_profile": brand.get("reply_style_profile"),
            "reasoning": brand.get("reply_style_reasoning"),
            "active_style": active_style,
            "learn_automatically": brand.get("reply_style_learn_automatically", True),
            "use_uploaded_only": brand.get("reply_style_use_uploaded_only", False),
            "last_generated_at": brand.get("reply_style_last_generated_at"),
            "approved_reply_count": approved_count,
            "eligible_for_learning": approved_count >= reply_style_service.MIN_APPROVED_REPLIES_TO_LEARN,
            "min_replies_required": reply_style_service.MIN_APPROVED_REPLIES_TO_LEARN,
            "presets": reply_style_presets.list_presets(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting reply style: {e}")
        raise HTTPException(status_code=500, detail="Failed to get reply style")


@router.patch("/{brand_id}/reply-style")
async def update_reply_style(
    brand_id: str,
    request: UpdateReplyStyleRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Update mode/preset/learning controls. Switching to 'learned' this way
    requires a profile to already exist — use switch-to-learned for the
    guided first transition, this endpoint is for toggling back and forth
    afterwards or changing controls."""
    try:
        brand = _get_owned_brand(brand_id, tenant.tenant_id)

        if request.mode == "learned" and not brand.get("reply_style_profile"):
            raise HTTPException(status_code=400, detail="No learned profile available yet.")
        if request.preset and request.preset not in reply_style_presets.PRESETS:
            raise HTTPException(status_code=400, detail="Unknown preset.")

        updates = {}
        if request.mode is not None:
            updates["reply_style_mode"] = request.mode
        if request.preset is not None:
            updates["reply_style_preset"] = request.preset
        if request.learn_automatically is not None:
            updates["reply_style_learn_automatically"] = request.learn_automatically
        if request.use_uploaded_only is not None:
            updates["reply_style_use_uploaded_only"] = request.use_uploaded_only

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = supabase_update("brands", {"id": f"eq.{brand_id}"}, updates)
        return {"success": True, "brand": _strip_secrets(result) if result else None}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating reply style: {e}")
        raise HTTPException(status_code=500, detail="Failed to update reply style")


@router.post("/{brand_id}/reply-style/regenerate")
async def regenerate_reply_style(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Manual regenerate — bypasses the 15-new-replies/7-day triggers but
    still requires the minimum approved-reply count."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        result = await reply_style_service.generate_learned_profile(brand_id, force=False)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error regenerating reply style: {e}")
        raise HTTPException(status_code=500, detail="Failed to regenerate reply style")


@router.post("/{brand_id}/reply-style/switch-to-learned")
async def switch_reply_style_to_learned(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Full replacement of the active preset with the learned profile — no
    blending, no confidence comparison, per spec."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        result = reply_style_service.switch_to_learned(brand_id)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error switching to learned style: {e}")
        raise HTTPException(status_code=500, detail="Failed to switch to learned style")


@router.get("/{brand_id}/reply-style/examples")
async def list_reply_examples(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Optional uploaded example replies — seed data for faster personalization."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        rows = supabase_select("reply_style_examples", {
            "brand_id": f"eq.{brand_id}", "order": "created_at.desc",
        })
        return {"examples": rows or []}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing reply examples: {e}")
        raise HTTPException(status_code=500, detail="Failed to list examples")


@router.post("/{brand_id}/reply-style/examples")
async def add_reply_example(
    brand_id: str,
    request: AddReplyExampleRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        result = supabase_insert("reply_style_examples", {
            "brand_id": brand_id,
            "content": request.content.strip(),
        })

        # Best-effort, never blocks the response: an uploaded example is a
        # deliberate, curated signal, so regenerate the learned profile right
        # away instead of waiting for the next opportunistic
        # regenerate_if_due() check (Settings page load). That check's
        # "due" logic only looks at NEW approved-reply volume once a profile
        # already exists (see regenerate_if_due), so a newly uploaded example
        # would otherwise never be picked up automatically at all. This never
        # changes reply_style_mode — becoming the active style is still the
        # merchant's own explicit "Switch to Learned Style" action.
        try:
            await reply_style_service.generate_learned_profile(brand_id, force=False)
        except Exception as e:
            logger.warning(f"[ReplyStyle] Profile regeneration after example upload failed: {e}")

        return {"success": True, "example": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding reply example: {e}")
        raise HTTPException(status_code=500, detail="Failed to add example")


@router.delete("/{brand_id}/reply-style/examples/{example_id}")
async def delete_reply_example(
    brand_id: str,
    example_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        supabase_delete("reply_style_examples", {"id": f"eq.{example_id}", "brand_id": f"eq.{brand_id}"})
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting reply example: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete example")


# ==================== Test Luna (onboarding activation) ====================

@router.post("/{brand_id}/test-reply")
async def test_reply(
    brand_id: str,
    request: TestReplyRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Runs a sample question through the real agent so a merchant can see an
    actual generated reply during onboarding, before any real customer email
    arrives. Same code path production replies use - not a canned response."""
    try:
        brand = _get_owned_brand(brand_id, tenant.tenant_id)

        from src.agent.customer_success_agent import customer_success_agent
        # Chat-mode formatting is driven by customer_info["channel"]="chat"
        # inside the prompt builder - no text prefix, so it can never leak
        # into a stored action's original_message if this test message
        # happens to trigger real staging.
        result = await customer_success_agent.process_customer_query(
            query=f"Customer: {request.message}",
            customer_info={"name": "Test Customer", "email": "test@example.com", "channel": "chat"},
            tenant_id=brand.get("tenant_id"),
            store_id=brand_id,
        )

        await supabase_service.log_onboarding_event(brand_id, "test_reply_generated", {
            "question": request.message,
        })

        return {
            "success": True,
            "question": request.message,
            "reply": result.get("reply_body"),
            "confidence_score": result.get("confidence_score"),
            # True only when every configured AI model (all Mistral + Groq keys)
            # was out of quota for this request — lets the onboarding UI show a
            # clear "AI is at capacity" notice instead of presenting the generic
            # customer-facing fallback copy as if it were a real Luna reply.
            "provider_outage": result.get("provider_outage", False),
            # Surfaces the agent's own low-confidence/escalate signal (set
            # whenever it couldn't ground the reply well) so the onboarding UI
            # can tell "Luna answered but wasn't confident she had grounded
            # store knowledge" apart from a genuine pass, instead of treating
            # any non-erroring HTTP response as a passed test regardless of
            # what the reply actually says.
            "escalate": bool(result.get("escalate")),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating test reply: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate test reply")


# ==================== Custom Email Automation ====================
# Merchant-configured confirmation emails for support actions. See
# src/services/email_automation_service.py for the trigger/variable
# contract and src/services/actions_service.py's _post_execution_notify
# for the (only) place these ever actually fire.

def _strip_automation(row: dict) -> dict:
    return {k: v for k, v in row.items() if k in {
        "id", "brand_id", "name", "trigger", "subject", "body",
        "enabled", "requires_approval", "created_at", "updated_at",
    }}


@router.get("/{brand_id}/email-automations")
async def list_email_automations(brand_id: str, tenant: TenantContext = Depends(get_current_tenant)):
    _get_owned_brand(brand_id, tenant.tenant_id)
    rows = supabase_select("email_automations", {"brand_id": f"eq.{brand_id}"}) or []
    return {
        "automations": [_strip_automation(r) for r in rows],
        "available_triggers": list(email_automation_service.SUPPORTED_TRIGGERS),
        "variables_by_trigger": {
            t: email_automation_service.variables_for_trigger(t)
            for t in email_automation_service.SUPPORTED_TRIGGERS
        },
    }


@router.post("/{brand_id}/email-automations")
async def create_email_automation(
    brand_id: str,
    payload: CreateEmailAutomationRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    _get_owned_brand(brand_id, tenant.tenant_id)
    existing = supabase_select("email_automations", {
        "brand_id": f"eq.{brand_id}", "trigger": f"eq.{payload.trigger}",
    })
    if existing:
        raise HTTPException(status_code=400, detail="An automation for this trigger already exists — edit it instead.")

    created = supabase_insert("email_automations", {
        "brand_id": brand_id,
        "name": payload.name,
        "trigger": payload.trigger,
        "subject": payload.subject,
        "body": payload.body,
        "enabled": payload.enabled,
        "requires_approval": payload.requires_approval,
    })
    if not created:
        raise HTTPException(status_code=500, detail="Failed to create automation")
    return {"success": True, "automation": _strip_automation(created)}


def _get_owned_automation(brand_id: str, automation_id: str) -> dict:
    rows = supabase_select("email_automations", {"id": f"eq.{automation_id}", "brand_id": f"eq.{brand_id}"})
    if not rows:
        raise HTTPException(status_code=404, detail="Automation not found")
    return rows[0]


@router.put("/{brand_id}/email-automations/{automation_id}")
async def update_email_automation(
    brand_id: str,
    automation_id: str,
    payload: UpdateEmailAutomationRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    _get_owned_brand(brand_id, tenant.tenant_id)
    _get_owned_automation(brand_id, automation_id)

    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    updated = supabase_update("email_automations", {"id": f"eq.{automation_id}"}, updates)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update automation")
    return {"success": True, "automation": _strip_automation(updated)}


@router.post("/{brand_id}/email-automations/{automation_id}/preview")
async def preview_email_automation(
    brand_id: str,
    automation_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    brand = _get_owned_brand(brand_id, tenant.tenant_id)
    automation = _get_owned_automation(brand_id, automation_id)

    variables = email_automation_service.sample_variables(automation["trigger"], brand.get("name", "your store"))
    return {
        "subject": email_automation_service.render_template(automation["subject"], variables),
        "body": email_automation_service.render_template(automation["body"], variables),
        "sample_variables": variables,
        "status": (
            "enabled_auto_send" if automation.get("enabled") and not automation.get("requires_approval")
            else "enabled_requires_approval" if automation.get("enabled")
            else "draft"
        ),
    }


@router.get("/{brand_id}/email-automations/pending")
async def list_pending_email_sends(brand_id: str, tenant: TenantContext = Depends(get_current_tenant)):
    _get_owned_brand(brand_id, tenant.tenant_id)
    rows = supabase_select("email_automation_pending", {
        "brand_id": f"eq.{brand_id}", "status": "eq.pending",
    }) or []
    return {"pending": rows}


@router.post("/{brand_id}/email-automations/pending/{pending_id}/send")
async def send_pending_email(
    brand_id: str,
    pending_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Merchant-approved send — the only way a requires_approval=true
    automation's email ever actually reaches the customer."""
    brand = _get_owned_brand(brand_id, tenant.tenant_id)
    rows = supabase_select("email_automation_pending", {
        "id": f"eq.{pending_id}", "brand_id": f"eq.{brand_id}", "status": "eq.pending",
    })
    if not rows:
        raise HTTPException(status_code=404, detail="Pending email not found")
    pending = rows[0]

    from src.services.brand_gmail_service import brand_gmail_service
    subject = pending["subject"] if pending["subject"].startswith("Re:") else f"Re: {pending['subject']}"
    send_result = await brand_gmail_service.send_email(brand, pending["to_email"], subject, pending["body"])
    if not send_result.get("success"):
        raise HTTPException(status_code=502, detail=send_result.get("error", "Failed to send email"))

    await email_automation_service.mark_pending_resolved(pending_id, brand_id, "sent")
    return {"success": True}


@router.post("/{brand_id}/email-automations/pending/{pending_id}/dismiss")
async def dismiss_pending_email(
    brand_id: str,
    pending_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    _get_owned_brand(brand_id, tenant.tenant_id)
    resolved = await email_automation_service.mark_pending_resolved(pending_id, brand_id, "dismissed")
    if not resolved:
        raise HTTPException(status_code=404, detail="Pending email not found")
    return {"success": True}
