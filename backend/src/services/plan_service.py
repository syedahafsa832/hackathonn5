"""
Plan / Trial / Usage Service
=============================
Single source of truth for subscription plans, trial status, super-admin
bypass, and usage limits. Every place that used to check a tenant's email or
plan by hand, or duplicate a limit-counting query, should call into this
module instead — specifically:

    check_limit(tenant_id, resource, email=None)   -> is one more X allowed?
    record_usage(tenant_id, resource)              -> count one X as used
    get_usage_summary(tenant_id)                   -> everything for the UI

Do not add a new `if plan == "free": ...` anywhere else in the codebase.
Add/adjust numbers in PLAN_LIMITS instead.

Design notes:
- No new tables for limits/usage. Trial dates and daily usage counters live on
  `tenants` (migration 022) — cheap: one row read + one conditional write per
  check, no aggregation queries, no cron job (usage resets lazily whenever
  read/written and the stored `usage_date` isn't today).
- Fails open on any DB error, consistent with the rest of the codebase's
  guardrails (email filters, auth checks, etc.) — a billing/limits bug must
  never be the reason a real customer is dropped.
- `subscriptions` table (migration 023) is Stripe-integration scaffolding
  only; this module does not read/write it yet.
"""
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from src.lib.supabase_client import supabase_select, supabase_update

logger = logging.getLogger(__name__)

TRIAL_DAYS = 14

# Plans that require manual (bank-transfer) payment and therefore expire —
# unlike free/trial/founding_free, which never need a renewal check.
PAID_PLANS = {"starter", "growth", "enterprise"}
PAID_PLAN_DURATION_DAYS = 30

# Landing page: "Founding Launch Pricing... Locked in for our first 20
# brands." Separate from FOUNDING_COHORT_CAP in auth_service.py, which caps
# free signups onto the legacy founding_free plan — this cap is about the
# discounted Starter/Growth price, tracked via tenants.founding_pricing_claimed_at
# (set once, kept forever, even if the plan later lapses — a claimed slot
# stays claimed). Scale has no founding price, so it never sets this.
FOUNDING_PAID_CAP = 20


def _parse_dt(v) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None

# Platform-level super admin(s) — always unlimited, regardless of plan/trial/
# billing state. Overridable via env for adding more without a code change;
# this email is always included so it can never be accidentally removed.
_env_admins = {e.strip().lower() for e in os.getenv("SUPER_ADMIN_EMAILS", "").split(",") if e.strip()}
SUPER_ADMIN_EMAILS = {"syedahafsa772@gmail.com", "syedahafsa1983@gmail.com"} | _env_admins

# Daily-counter resources (tracked via tenants.usage_*_today, reset per day)
# vs. point-in-time count resources (brands/users/gmail — counted live from
# their own tables, no daily reset).
_DAILY_RESOURCES = {"tickets", "ai_replies", "emails", "shopify_actions"}
_COUNT_RESOURCES = {"brands", "users", "gmail_accounts"}

_DAILY_LIMIT_KEY = {
    "tickets": "tickets_per_day",
    "ai_replies": "ai_replies_per_day",
    "emails": "emails_per_day",
    "shopify_actions": "shopify_actions_per_day",
}
_DAILY_USAGE_COLUMN = {
    "tickets": "usage_tickets_today",
    "ai_replies": "usage_ai_replies_today",
    "emails": "usage_emails_today",
    "shopify_actions": "usage_shopify_actions_today",
}
_COUNT_LIMIT_KEY = {
    "brands": "brands",
    "users": "users",
    "gmail_accounts": "gmail_accounts",
}

# Centralized plan limits — the ONLY place these numbers should live.
# `None` means unlimited for that dimension.
PLAN_LIMITS: Dict[str, Dict[str, Optional[int]]] = {
    "trial": {
        # Full evaluation access, but capped so a throwaway signup can't burn
        # AI/API spend — see TASK 1 (trial abuse protection).
        "tickets_per_day": None,
        "ai_replies_per_day": 500,
        "emails_per_day": 1000,
        "shopify_actions_per_day": 100,
        "brands": 1,
        "users": 1,
        "gmail_accounts": 1,
        "label": "Trial",
    },
    "free": {
        "tickets_per_day": 10,
        # Mirrors tickets_per_day, not a separate customer-facing cap — the
        # tickets check runs first in message_processor.py and always trips
        # before this one, so it's never the effective bottleneck. Kept equal
        # so the pricing page can show a single "tickets/day" number without
        # that number being inaccurate for AI-handled tickets specifically.
        "ai_replies_per_day": 10,
        "emails_per_day": 50,
        "shopify_actions_per_day": 5,
        "brands": 1,
        "users": 1,
        "gmail_accounts": 1,
        "price_monthly": 0,
        "label": "Free",
    },
    "starter": {
        # Landing page: "500 conversations per month" — a real monthly cap,
        # not daily. tickets_per_month (not tickets_per_day) is what tells
        # check_limit()/record_usage() to use the monthly counter instead of
        # the daily one; see usage_month/usage_tickets_this_month (migration
        # 034). ai_replies_per_day is uncapped so it can never become a
        # surprise second bottleneck undercutting the advertised number —
        # the monthly conversation cap is the only real constraint.
        "tickets_per_month": 500,
        "ai_replies_per_day": None,
        "emails_per_day": 2000,
        "shopify_actions_per_day": 200,
        "brands": 1,
        "users": 1,
        "gmail_accounts": 1,
        "price_monthly": 99,
        "price_monthly_original": 149,
        "label": "Starter",
    },
    "growth": {
        # Landing page: "unlimited conversations".
        "tickets_per_month": None,
        "ai_replies_per_day": None,
        "emails_per_day": 20000,
        "shopify_actions_per_day": 2000,
        # Landing page: "Up to 3 brands." Multi-brand is a manual,
        # onboarding-time setup (see the note on the Upgrade page) — there's
        # no self-serve brand switcher yet, but the cap itself is real and
        # enforced the same way as every other plan's brand limit.
        "brands": 3,
        "users": 1,
        "gmail_accounts": 3,
        "price_monthly": 249,
        "price_monthly_original": 349,
        "label": "Growth",
    },
    "enterprise": {
        "tickets_per_day": None,
        "ai_replies_per_day": None,
        "emails_per_day": None,
        "shopify_actions_per_day": None,
        "brands": None,
        "users": None,
        "gmail_accounts": None,
        # None here means "custom / contact us", same convention as the
        # other None values on this plan meaning "uncapped" — the pricing
        # page renders this plan with no fixed numbers at all. Internal plan
        # id kept as "enterprise" (DB rows, PAID_PLANS, etc. all reference
        # this string) — only the customer-facing label changed to match
        # the landing page's rename to "Scale".
        "price_monthly": None,
        "label": "Scale",
    },
    # Legacy plan predating this system (migration 021) — kept so existing
    # founding-cohort tenants don't change behavior; new signups never get it.
    "founding_free": {
        "tickets_per_day": 5,
        "ai_replies_per_day": 25,
        "emails_per_day": 100,
        "shopify_actions_per_day": 10,
        "brands": 1,
        "users": 1,
        "gmail_accounts": 1,
        "label": "Founding (Free)",
    },
}

DEFAULT_PLAN = "free"


def is_super_admin(email: Optional[str]) -> bool:
    """Centralized super-admin check. Use this everywhere instead of comparing
    an email inline — it's the only place the admin list should ever live."""
    if not email:
        return False
    return email.strip().lower() in SUPER_ADMIN_EMAILS


def get_trial_dates(tenant: dict) -> tuple[Optional[datetime], Optional[datetime]]:
    return _parse_dt(tenant.get("trial_start_at")), _parse_dt(tenant.get("trial_end_at"))


def get_paid_plan_expiry(tenant: dict) -> Optional[datetime]:
    """When this tenant's current paid-plan activation expires, or None if
    plan_activated_at was never set (e.g. legacy rows from before this)."""
    activated_at = _parse_dt(tenant.get("plan_activated_at"))
    if not activated_at:
        return None
    return activated_at + timedelta(days=PAID_PLAN_DURATION_DAYS)


def _resolve_plan(tenant: dict) -> str:
    """Effective plan for this tenant right now. If the tenant is on 'trial'
    and the trial has expired, or on a paid plan more than 30 days past its
    plan_activated_at, this also persists the downgrade to 'free' — the one
    place expiration is actually enforced for either case.

    plan_activated_at is deliberately left in place after a paid-plan
    downgrade (not cleared) so callers can tell "this tenant's plan just
    lapsed" apart from "this tenant has never paid" — see get_usage_summary's
    was_previously_paid.
    """
    plan = (tenant.get("plan") or DEFAULT_PLAN).lower()

    if plan == "trial":
        _, trial_end = get_trial_dates(tenant)
        if trial_end and datetime.now(timezone.utc) >= trial_end:
            try:
                supabase_update("tenants", {"id": f"eq.{tenant['id']}"}, {"plan": DEFAULT_PLAN})
                logger.info(f"[Plan] Tenant {tenant['id']} trial expired — moved to {DEFAULT_PLAN}")
            except Exception as e:
                logger.warning(f"[Plan] Failed to downgrade expired trial for {tenant.get('id')}: {e}")
            return DEFAULT_PLAN
        return "trial"

    if plan in PAID_PLANS:
        expires_at = get_paid_plan_expiry(tenant)
        if expires_at and datetime.now(timezone.utc) >= expires_at:
            try:
                supabase_update("tenants", {"id": f"eq.{tenant['id']}"}, {"plan": DEFAULT_PLAN})
                logger.info(f"[Plan] Tenant {tenant['id']} paid plan '{plan}' expired 30 days after activation — reverted to {DEFAULT_PLAN}")
            except Exception as e:
                logger.warning(f"[Plan] Failed to revert expired paid plan for {tenant.get('id')}: {e}")
            return DEFAULT_PLAN
        return plan

    return plan if plan in PLAN_LIMITS else DEFAULT_PLAN


def _reset_usage_if_new_day(tenant: dict) -> dict:
    """Returns tenant's daily usage counters, zeroed (and persisted) if the
    stored usage_date isn't today."""
    today = datetime.now(timezone.utc).date().isoformat()
    if tenant.get("usage_date") == today:
        return {
            "tickets": tenant.get("usage_tickets_today") or 0,
            "ai_replies": tenant.get("usage_ai_replies_today") or 0,
            "emails": tenant.get("usage_emails_today") or 0,
            "shopify_actions": tenant.get("usage_shopify_actions_today") or 0,
        }
    reset = {
        "usage_date": today,
        "usage_tickets_today": 0,
        "usage_ai_replies_today": 0,
        "usage_emails_today": 0,
        "usage_shopify_actions_today": 0,
    }
    try:
        supabase_update("tenants", {"id": f"eq.{tenant['id']}"}, reset)
    except Exception as e:
        logger.warning(f"[Plan] Failed to reset daily usage for {tenant.get('id')}: {e}")
    return {"tickets": 0, "ai_replies": 0, "emails": 0, "shopify_actions": 0}


def _next_reset_at() -> str:
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return tomorrow.isoformat()


def _reset_monthly_tickets_if_new_month(tenant: dict) -> int:
    """Returns this tenant's tickets-this-month count, zeroed (and
    persisted) if the stored usage_month isn't the current calendar month.
    Mirrors _reset_usage_if_new_day, but for plans whose PLAN_LIMITS entry
    defines tickets_per_month instead of tickets_per_day (currently just
    starter's 500-conversations-per-month cap)."""
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    if tenant.get("usage_month") == current_month:
        return tenant.get("usage_tickets_this_month") or 0
    try:
        supabase_update("tenants", {"id": f"eq.{tenant['id']}"}, {
            "usage_month": current_month,
            "usage_tickets_this_month": 0,
        })
    except Exception as e:
        logger.warning(f"[Plan] Failed to reset monthly ticket usage for {tenant.get('id')}: {e}")
    return 0


def _next_month_reset_at() -> str:
    now = datetime.now(timezone.utc)
    first_of_next_month = (now.replace(day=1) + timedelta(days=32)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return first_of_next_month.isoformat()


def get_founding_slots_remaining() -> int:
    """How many of the first FOUNDING_PAID_CAP founding-priced Starter/Growth
    slots are still unclaimed. Used by the pricing page to decide whether to
    show the founding (struck-through) price or the regular price."""
    try:
        claimed = supabase_select("tenants", {
            "founding_pricing_claimed_at": "not.is.null",
            "select": "id",
        }) or []
        return max(0, FOUNDING_PAID_CAP - len(claimed))
    except Exception as e:
        logger.warning(f"[Plan] get_founding_slots_remaining failed: {e} — assuming slots remain")
        return FOUNDING_PAID_CAP


def _count_resource(tenant_id: str, resource: str) -> int:
    """Live row count for count-based resources. Kept to single, cheap,
    already-filtered selects — no joins, no aggregation."""
    if resource == "brands":
        return len(supabase_select("brands", {"tenant_id": f"eq.{tenant_id}", "select": "id"}) or [])
    if resource == "gmail_accounts":
        return len(supabase_select("brands", {
            "tenant_id": f"eq.{tenant_id}", "gmail_connected": "is.true", "select": "id",
        }) or [])
    if resource == "users":
        # +1 for the tenant/owner account itself, which has no row in `users`
        # for v1 tenants — "users: 1" means solo, zero additional invites.
        team = supabase_select("users", {"organization_id": f"eq.{tenant_id}", "select": "id"}) or []
        return len(team) + 1
    raise ValueError(f"Unknown count resource: {resource}")


def check_limit(tenant_id: Optional[str], resource: str, email: Optional[str] = None) -> Dict[str, Any]:
    """The one function every gate in the app should call.

    resource: one of "tickets", "ai_replies", "emails", "shopify_actions"
              (daily, auto-resetting) or "brands", "users", "gmail_accounts"
              (live count against a static cap).

    Returns: allowed (bool), remaining (int|None), reason (str|None),
             limit (int|None), used (int), plan (str), reset_at (iso str|None),
             upgrade_required (bool)
    """
    base = {
        "allowed": True,
        "remaining": None,
        "reason": None,
        "limit": None,
        "used": 0,
        "plan": "trial",
        "reset_at": _next_reset_at() if resource in _DAILY_RESOURCES else None,
        "upgrade_required": False,
    }

    if not tenant_id:
        return base  # unresolvable tenant — fail open, matches existing behavior

    try:
        tenants = supabase_select("tenants", {"id": f"eq.{tenant_id}"})
        if not tenants:
            return base
        tenant = tenants[0]

        # Admin bypass happens BEFORE any trial/plan/usage check — unconditional.
        if is_super_admin(email) or is_super_admin(tenant.get("email")):
            base["plan"] = "super_admin"
            return base

        plan = _resolve_plan(tenant)
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS[DEFAULT_PLAN])
        base["plan"] = plan

        # Starter's conversation cap is monthly, not daily (landing page:
        # "500 conversations per month") — a plan signals this by defining
        # tickets_per_month instead of tickets_per_day. Everything else
        # (free/trial/founding_free, plus growth/enterprise's unlimited
        # tickets_per_day) goes through the existing daily path unchanged.
        is_monthly_tickets = resource == "tickets" and "tickets_per_month" in limits
        if is_monthly_tickets:
            limit = limits["tickets_per_month"]
            if limit is None:
                return base
            used = _reset_monthly_tickets_if_new_month(tenant)
            base["reset_at"] = _next_month_reset_at()
        elif resource in _DAILY_RESOURCES:
            limit = limits.get(_DAILY_LIMIT_KEY[resource])
            if limit is None:
                return base
            usage = _reset_usage_if_new_day(tenant)
            used = usage[resource]
        elif resource in _COUNT_RESOURCES:
            limit = limits.get(_COUNT_LIMIT_KEY[resource])
            if limit is None:
                return base
            used = _count_resource(tenant_id, resource)
        else:
            raise ValueError(f"Unknown resource: {resource}")

        remaining = max(0, limit - used)
        base.update({
            "allowed": used < limit,
            "remaining": remaining,
            "limit": limit,
            "used": used,
            "upgrade_required": used >= limit,
        })
        if used >= limit:
            base["reason"] = (
                "monthly_limit_reached" if is_monthly_tickets
                else "daily_limit_reached" if resource in _DAILY_RESOURCES
                else "plan_limit_reached"
            )
        return base

    except Exception as e:
        logger.warning(f"[Plan] check_limit({resource}) failed for tenant {tenant_id}: {e} — allowing")
        return base


def record_usage(tenant_id: Optional[str], resource: str) -> None:
    """Increment the usage counter for a daily (or, for tickets on a
    tickets_per_month plan, monthly) resource. No-op for count-based
    resources (brands/users/gmail_accounts are counted live from their own
    tables, nothing to increment)."""
    if not tenant_id or resource not in _DAILY_RESOURCES:
        return
    try:
        tenants = supabase_select("tenants", {"id": f"eq.{tenant_id}"})
        if not tenants:
            return
        tenant = tenants[0]

        if resource == "tickets":
            plan = _resolve_plan(tenant)
            limits = PLAN_LIMITS.get(plan, PLAN_LIMITS[DEFAULT_PLAN])
            if "tickets_per_month" in limits:
                used = _reset_monthly_tickets_if_new_month(tenant)
                supabase_update("tenants", {"id": f"eq.{tenant_id}"}, {"usage_tickets_this_month": used + 1})
                return

        usage = _reset_usage_if_new_day(tenant)
        column = _DAILY_USAGE_COLUMN[resource]
        supabase_update("tenants", {"id": f"eq.{tenant_id}"}, {column: usage[resource] + 1})
    except Exception as e:
        logger.warning(f"[Plan] Failed to record {resource} usage for tenant {tenant_id}: {e}")


def build_limit_error(resource: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Standard 402 error body shape used by every limit-gated endpoint."""
    return {
        "error": "PLAN_LIMIT_REACHED",
        "resource": resource,
        "current": result.get("used"),
        "limit": result.get("limit"),
        "upgrade_required": True,
    }


# ---- Backward-compatible thin wrappers (existing call sites) ----------------

def can_process_ticket(tenant_id: Optional[str], email: Optional[str] = None) -> Dict[str, Any]:
    return check_limit(tenant_id, "tickets", email)


def check_brand_limit(tenant_id: str, email: Optional[str] = None) -> Dict[str, Any]:
    r = check_limit(tenant_id, "brands", email)
    return {"allowed": r["allowed"], "limit": r["limit"], "used": r["used"]}


def check_gmail_limit(tenant_id: str, email: Optional[str] = None) -> Dict[str, Any]:
    r = check_limit(tenant_id, "gmail_accounts", email)
    return {"allowed": r["allowed"], "limit": r["limit"], "used": r["used"]}


def check_user_limit(tenant_id: str, email: Optional[str] = None) -> Dict[str, Any]:
    r = check_limit(tenant_id, "users", email)
    return {"allowed": r["allowed"], "limit": r["limit"], "used": r["used"]}


def record_ticket_created(tenant_id: Optional[str]) -> None:
    record_usage(tenant_id, "tickets")


def record_ai_reply(tenant_id: Optional[str]) -> None:
    record_usage(tenant_id, "ai_replies")


def record_email_processed(tenant_id: Optional[str]) -> None:
    record_usage(tenant_id, "emails")


def record_shopify_action(tenant_id: Optional[str]) -> None:
    record_usage(tenant_id, "shopify_actions")


def get_usage_summary(tenant_id: str) -> Dict[str, Any]:
    """Everything the dashboard's plan/usage widget needs in one call."""
    try:
        tenants = supabase_select("tenants", {"id": f"eq.{tenant_id}"})
        if not tenants:
            return {"plan": DEFAULT_PLAN, "is_super_admin": False}
        tenant = tenants[0]

        admin = is_super_admin(tenant.get("email"))
        plan = "super_admin" if admin else _resolve_plan(tenant)
        # Admin has no PLAN_LIMITS entry of its own — display it as unlimited
        # using the "enterprise" numbers rather than duplicating a second
        # all-None dict.
        limits = PLAN_LIMITS["enterprise"] if admin else PLAN_LIMITS.get(plan, PLAN_LIMITS[DEFAULT_PLAN])
        usage = _reset_usage_if_new_day(tenant)

        # Starter tracks tickets monthly (see check_limit/record_usage) — the
        # daily usage_today.tickets counter is never incremented for it, so
        # tickets_used/tickets_limit/upgrade_required below must read from
        # the monthly counter instead, or they'd silently show 0/unlimited
        # regardless of real usage.
        is_monthly_tickets = "tickets_per_month" in limits
        if is_monthly_tickets:
            tickets_limit = limits.get("tickets_per_month")
            tickets_used = _reset_monthly_tickets_if_new_month(tenant)
        else:
            tickets_limit = limits.get("tickets_per_day")
            tickets_used = usage["tickets"]

        trial_start, trial_end = get_trial_dates(tenant)
        trial_days_remaining = None
        if plan == "trial" and trial_end:
            trial_days_remaining = max(0, (trial_end - datetime.now(timezone.utc)).days)

        plan_days_remaining = None
        if plan in PAID_PLANS:
            expires_at = get_paid_plan_expiry(tenant)
            if expires_at:
                plan_days_remaining = max(0, (expires_at - datetime.now(timezone.utc)).days)

        # plan_activated_at survives a paid-plan revert (see _resolve_plan), so
        # its presence on a tenant now sitting on the free plan means their
        # paid plan lapsed — distinct from a tenant that has never paid, so
        # the frontend can show "resubscribe" instead of a generic upgrade nag.
        was_previously_paid = (not admin) and plan == DEFAULT_PLAN and bool(tenant.get("plan_activated_at"))

        ai_limit = limits.get("ai_replies_per_day")
        emails_limit = limits.get("emails_per_day")
        return {
            "plan": plan,
            "plan_label": limits.get("label", plan.title()),
            "is_super_admin": admin,
            "trial_days_remaining": trial_days_remaining,
            "plan_activated_at": tenant.get("plan_activated_at"),
            "plan_days_remaining": plan_days_remaining,
            "was_previously_paid": was_previously_paid,
            "usage_today": usage,
            "limits": limits,
            "tickets_used_this_period": tickets_used,
            "tickets_period": "month" if is_monthly_tickets else "day",
            "tickets_remaining_today": None if tickets_limit is None else max(0, tickets_limit - tickets_used),
            "ai_replies_remaining_today": None if ai_limit is None else max(0, ai_limit - usage["ai_replies"]),
            "emails_remaining_today": None if emails_limit is None else max(0, emails_limit - usage["emails"]),
            "upgrade_required": False if admin or tickets_limit is None else tickets_used >= tickets_limit,
        }
    except Exception as e:
        logger.warning(f"[Plan] get_usage_summary failed for tenant {tenant_id}: {e}")
        return {"plan": DEFAULT_PLAN, "is_super_admin": False}
