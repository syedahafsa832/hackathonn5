"""
Custom Email Automation
========================
Merchant-configured confirmation emails for support actions (cancellation,
refund, exchange, address change).

Deliberately reuses the existing action-execution success path
(_post_execution_notify in actions_service.py) as its only trigger point,
and the existing brand Gmail send (brand_gmail_service.send_email) as its
only delivery mechanism — no second email system, no new trigger surface.
A custom automation can only ever fire from inside that same
already-approved, already-executed code path, so it structurally cannot
bypass action approval, and can never fire for a failed or still-pending
action.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update

logger = logging.getLogger(__name__)

# Every trigger maps 1:1 to an action_type actions_service.py can actually
# execute and report success/failure for. "Shipping/update notification"
# and "customer follow-up" are intentionally not offered yet — there is no
# existing success/failure event in this codebase to hook them to safely.
SUPPORTED_TRIGGERS = ("cancel_order", "refund", "exchange", "change_address")

# Variables resolvable for every trigger, plus the per-trigger extra. This
# is the ONLY thing {{...}} substitution ever reads from — a fixed,
# backend-computed dict, never a raw ticket/order/action record — so a
# custom template can never reach an arbitrary database field.
COMMON_VARIABLES = ("customer_name", "order_number", "order_status", "brand_name")
TRIGGER_EXTRA_VARIABLES = {"refund": ("refund_amount",)}


def variables_for_trigger(trigger: str) -> list:
    return list(COMMON_VARIABLES) + list(TRIGGER_EXTRA_VARIABLES.get(trigger, ()))


def render_template(template: str, variables: dict) -> str:
    """Literal {{name}} substitution only — never str.format/eval/Jinja,
    so a merchant-authored template can never reach an attribute or
    database field beyond what's explicitly passed in `variables`."""
    if not template:
        return ""
    rendered = template
    for key, value in variables.items():
        rendered = rendered.replace("{{" + key + "}}", "" if value is None else str(value))
    return rendered


def get_enabled_automation(brand_id: str, trigger: str) -> Optional[dict]:
    """The one enabled automation configured for this brand+trigger, or
    None (falls back to the existing hardcoded confirmation copy)."""
    if trigger not in SUPPORTED_TRIGGERS:
        return None
    rows = supabase_select("email_automations", {
        "brand_id": f"eq.{brand_id}",
        "trigger": f"eq.{trigger}",
        "enabled": "is.true",
    }) or []
    return rows[0] if rows else None


def queue_pending_send(automation: dict, action: dict, to_email: str, subject: str, body: str) -> dict:
    """Record a rendered email awaiting merchant approval. Never sent from
    here — sending only ever happens via approve_pending_send() below,
    after a merchant explicitly clicks Send in the dashboard."""
    return supabase_insert("email_automation_pending", {
        "automation_id": automation["id"],
        "brand_id": automation["brand_id"],
        "action_id": action.get("id"),
        "ticket_id": action.get("ticket_id"),
        "to_email": to_email,
        "subject": subject,
        "body": body,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def order_status_label(trigger: str, execution_result: dict) -> str:
    """A short, real status string derived from the actual execution
    result — never a guess, never independent of what Shopify reported."""
    execution_result = execution_result or {}
    if trigger == "cancel_order":
        return "Cancelled"
    if trigger == "refund":
        return "Refunded"
    if trigger == "exchange":
        if execution_result.get("manual_action_required"):
            return "Exchange in progress"
        if execution_result.get("completed"):
            return "Exchange confirmed"
        return "Exchange awaiting payment"
    if trigger == "change_address":
        return "Address update in progress" if execution_result.get("manual_action_required") else "Address updated"
    return ""


_SAMPLE_EXECUTION_RESULT = {
    "cancel_order": {},
    "refund": {"amount": 49.00},
    "exchange": {"completed": True},
    "change_address": {},
}


def sample_variables(trigger: str, brand_name: str) -> dict:
    """Realistic example data for the preview endpoint — never a real
    customer, order, or amount."""
    execution_result = _SAMPLE_EXECUTION_RESULT.get(trigger, {})
    variables = {
        "customer_name": "Alex",
        "order_number": "#1234",
        "brand_name": brand_name,
        "order_status": order_status_label(trigger, execution_result),
    }
    if trigger == "refund":
        variables["refund_amount"] = "PKR 49.00"
    return variables


async def mark_pending_resolved(pending_id: str, brand_id: str, status: str) -> Optional[dict]:
    """status is 'sent' or 'dismissed'. Scoped to brand_id so a pending
    send can only ever be resolved by the tenant that owns its brand."""
    updated = supabase_update(
        "email_automation_pending",
        {"id": f"eq.{pending_id}", "brand_id": f"eq.{brand_id}", "status": "eq.pending"},
        {"status": status, "resolved_at": datetime.now(timezone.utc).isoformat()},
    )
    return updated or None
