"""
Financial Action Audit + Idempotency (H2)
============================================
Shared by the refund and cancel-order endpoints in v2_tickets.py.

Two responsibilities, one table (financial_action_audit_log — see migration
027): every execution of a financial action is recorded (IP, timestamp, user,
action type, ticket) for audit purposes, and that same record is what makes
a client-supplied Idempotency-Key actually work — a retried request with the
same key returns the original recorded result instead of re-executing the
Shopify call.

Also provides a simple per-organization rate limiter for these endpoints,
modeled on the existing in-memory limiter in v2_chat_widget.py — a plain
dict is fully effective here since the app runs under a single gunicorn
worker (see backend/Dockerfile), same reasoning as everywhere else in this
codebase that uses one.
"""
import logging
import time
from collections import defaultdict
from typing import Optional

from src.lib.supabase_client import supabase_select, supabase_insert

logger = logging.getLogger(__name__)


# ── Idempotency ────────────────────────────────────────────────────────────

def get_cached_result(ticket_id: str, action_type: str, idempotency_key: str) -> Optional[dict]:
    """If this exact (ticket, action_type, idempotency_key) already ran,
    return its recorded result so the caller can replay it instead of
    re-executing the financial action."""
    if not idempotency_key:
        return None
    rows = supabase_select("financial_action_audit_log", {
        "ticket_id": f"eq.{ticket_id}",
        "action_type": f"eq.{action_type}",
        "idempotency_key": f"eq.{idempotency_key}",
        "order": "created_at.asc",
        "limit": "1",
    })
    return rows[0] if rows else None


def record_financial_action(
    *,
    ticket_id: str,
    tenant_id: Optional[str],
    brand_id: Optional[str],
    action_type: str,
    idempotency_key: Optional[str],
    user_id: Optional[str],
    user_email: Optional[str],
    ip_address: Optional[str],
    status: str,
    result: Optional[dict] = None,
    error_detail: Optional[str] = None,
) -> None:
    """Append-only audit record (see migration 027 — UPDATE/DELETE are
    revoked at the database level, not just avoided by convention). Never
    raises for a duplicate-key insert race — that just means a concurrent
    request already recorded this exact idempotency key, which is the
    guarantee working as intended, not a failure the caller needs to see."""
    try:
        supabase_insert("financial_action_audit_log", {
            "ticket_id": ticket_id,
            "tenant_id": tenant_id,
            "brand_id": brand_id,
            "action_type": action_type,
            "idempotency_key": idempotency_key,
            "user_id": user_id,
            "user_email": user_email,
            "ip_address": ip_address,
            "status": status,
            "result": result,
            "error_detail": error_detail,
        })
    except Exception as e:
        if "23505" not in str(e) and "409" not in str(e):
            logger.error(f"[FinancialAudit] Failed to record {action_type} for ticket {ticket_id}: {e}")


# ── Per-organization rate limiting ──────────────────────────────────────────

_RATE_WINDOW_SECONDS = 60
_RATE_MAX_PER_ORG = 10
_rate_buckets: dict = defaultdict(list)


def check_org_rate_limit(org_id: str) -> bool:
    """True if this org is still under the limit (and records this call
    toward it); False if the org has hit 10 financial-action requests in
    the last 60 seconds."""
    now = time.monotonic()
    cutoff = now - _RATE_WINDOW_SECONDS
    bucket = [t for t in _rate_buckets[org_id] if t > cutoff]
    if len(bucket) >= _RATE_MAX_PER_ORG:
        _rate_buckets[org_id] = bucket
        return False
    bucket.append(now)
    _rate_buckets[org_id] = bucket
    return True
