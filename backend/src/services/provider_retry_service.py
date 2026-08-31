"""
Provider-outage retry queue
============================
Persists a customer message's AI response as retryable work when every
configured AI provider failed for that request (ai_provider_manager
exhausted its full Mistral+Groq failover chain — see
customer_success_agent.py's _get_provider_failure_response), instead of the
previous behavior of immediately and permanently marking the ticket
"escalated". Free-tier provider quota/rate-limit exhaustion is expected to
recover on its own; a merchant should not have to notice and manually
retry every such message (see backend/migrations/058_ai_response_retries.sql
for the schema and rationale).

Pure queue mechanics only (classify/enqueue/claim/reschedule/mark) — the
actual regeneration-and-send orchestration lives on
UnifiedMessageProcessor.retry_pending_response() in message_processor.py,
since it needs to reuse that class's existing routing/decision/send
methods rather than duplicate them here.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update

logger = logging.getLogger(__name__)

# 'retryable' (rate limit/quota/timeout/5xx) gets the full bounded schedule —
# spans ~3.5 hours, matching "free-tier quota resets on its own". 'fast_fail'
# (an unclassified reason — e.g. an invalid key/model) gets a short schedule
# so a genuine misconfiguration still reaches a human quickly rather than
# sitting unescalated for hours. Either way, retries are always bounded —
# never indefinite, regardless of cause.
RETRYABLE_SCHEDULE_MINUTES = [2, 5, 15, 30, 60, 120]
FAST_FAIL_SCHEDULE_MINUTES = [2, 10]

# Reused from ai_provider_manager._describe() — never re-derived from raw
# provider exception text here, so the two classifiers can't drift.
_RETRYABLE_REASONS = {"rate_limited", "quota_exceeded", "timeout"}


def classify_outage(attempts: List[Dict[str, str]]) -> str:
    """attempts is AllProvidersFailedError.attempts: [{"label", "reason"}, ...],
    one entry per provider that failed this request. Optimistic: if ANY
    attempt looks transient, treat the whole outage as retryable — a
    customer message deserves the full retry schedule unless every provider
    agreed the failure was non-transient."""
    for attempt in attempts or []:
        reason = (attempt.get("reason") or "")
        if reason in _RETRYABLE_REASONS or reason.startswith("provider_error_5"):
            return "retryable"
    return "fast_fail"


def _schedule_for(tier: str) -> List[int]:
    return RETRYABLE_SCHEDULE_MINUTES if tier == "retryable" else FAST_FAIL_SCHEDULE_MINUTES


def enqueue_retry(ticket_id: str, brand_id: Optional[str], attempts: List[Dict[str, str]],
                   last_error: str) -> Optional[Dict[str, Any]]:
    """Queue one retry job for this ticket. Returns the created row, or None
    if a retry is already active for this ticket (the unique partial index
    on (ticket_id) WHERE status IN (pending, processing) enforces this —
    a second outage on the same ticket before the first retry resolves
    must not queue a duplicate job that could regenerate/send a second
    response) or on any other failure (fail-open — the caller still falls
    back to a normal escalation in that case)."""
    tier = classify_outage(attempts)
    schedule = _schedule_for(tier)
    next_retry_at = (datetime.now(timezone.utc) + timedelta(minutes=schedule[0])).isoformat()
    try:
        row = supabase_insert("ai_response_retries", {
            "ticket_id": ticket_id,
            "brand_id": brand_id,
            "outage_tier": tier,
            "max_retries": len(schedule),
            "next_retry_at": next_retry_at,
            "last_error": (last_error or "")[:500],
        })
        logger.info(f"[ProviderRetry] Queued ticket={ticket_id} tier={tier} next_retry_at={next_retry_at}")
        return row
    except Exception as e:
        if "409" in str(e):
            logger.info(f"[ProviderRetry] Retry already active for ticket={ticket_id} — not duplicating")
        else:
            logger.warning(f"[ProviderRetry] Failed to queue retry for ticket={ticket_id}: {e}")
        return None


# A real retry_pending_response() call involves at most a handful of
# LLM/Shopify/RAG round-trips — seconds, not minutes. A row still sitting
# at status='processing' after this long is almost certainly orphaned: the
# worker instance that claimed it died mid-flight (a deploy, crash, or
# restart) before ever calling mark_succeeded/mark_cancelled/
# reschedule_or_exhaust. Confirmed live: a Render deploy rolled the
# container over 4 seconds after a worker claimed a job, permanently
# stranding it at 'processing' with no reclaim path - claim_due_retries()
# below only ever looked at status='pending', so an orphaned row was
# invisible to every future poll and needed manual DB intervention.
STALE_PROCESSING_MINUTES = 5


def reclaim_stale_processing() -> int:
    """Puts back to 'pending' (next_retry_at=now) any row still 'processing'
    more than STALE_PROCESSING_MINUTES after it was claimed (claim_due_retries
    sets updated_at at claim time). Returns the number reclaimed. Uses the
    same conditional-UPDATE-WHERE-status pattern as claim_due_retries, so a
    row a still-alive worker finishes between the select and the update here
    simply won't match and is safely skipped."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=STALE_PROCESSING_MINUTES)).isoformat()
    stale = supabase_select("ai_response_retries", {
        "status": "eq.processing",
        "updated_at": f"lt.{cutoff}",
    })
    now_iso = datetime.now(timezone.utc).isoformat()
    reclaimed = 0
    for row in stale or []:
        result = supabase_update(
            "ai_response_retries",
            {"id": f"eq.{row['id']}", "status": "eq.processing"},
            {"status": "pending", "next_retry_at": now_iso,
             "last_error": "reclaimed — worker died mid-processing (deploy/crash/restart)"},
        )
        if result:
            reclaimed += 1
    if reclaimed:
        logger.warning(f"[ProviderRetry] Reclaimed {reclaimed} stale 'processing' row(s) back to pending")
    return reclaimed


def claim_due_retries(limit: int = 20) -> List[Dict[str, Any]]:
    """Atomically claims up to `limit` due rows (next_retry_at <= now,
    status='pending') by flipping status to 'processing' one row at a time
    via a conditional UPDATE ... WHERE status='pending' — an UPDATE that
    matches zero rows (already claimed by a concurrent call) is silently
    skipped. Single-process deployment assumption, same tradeoff already
    made by shopify_import_service.py's in-memory _import_status."""
    now_iso = datetime.now(timezone.utc).isoformat()
    due = supabase_select("ai_response_retries", {
        "status": "eq.pending",
        "next_retry_at": f"lte.{now_iso}",
        "order": "next_retry_at.asc",
        "limit": str(limit),
    })
    claimed: List[Dict[str, Any]] = []
    for row in due or []:
        result = supabase_update(
            "ai_response_retries",
            {"id": f"eq.{row['id']}", "status": "eq.pending"},
            {"status": "processing", "updated_at": now_iso},
        )
        if result:
            row = {**row, "status": "processing"}
            claimed.append(row)
    return claimed


def mark_succeeded(row_id: str) -> None:
    supabase_update("ai_response_retries", {"id": f"eq.{row_id}"}, {
        "status": "succeeded", "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def mark_cancelled(row_id: str, reason: str) -> None:
    supabase_update("ai_response_retries", {"id": f"eq.{row_id}"}, {
        "status": "cancelled", "last_error": reason[:500],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def reschedule_or_exhaust(row: Dict[str, Any], error: str) -> str:
    """Called when a claimed retry attempt itself fails (still no provider
    available). Returns 'rescheduled' or 'exhausted'."""
    retry_count = (row.get("retry_count") or 0) + 1
    max_retries = row.get("max_retries") or len(RETRYABLE_SCHEDULE_MINUTES)
    now = datetime.now(timezone.utc)
    if retry_count >= max_retries:
        supabase_update("ai_response_retries", {"id": f"eq.{row['id']}"}, {
            "status": "exhausted", "retry_count": retry_count, "last_error": (error or "")[:500],
            "updated_at": now.isoformat(),
        })
        logger.warning(f"[ProviderRetry] Exhausted retries for ticket={row.get('ticket_id')} after {retry_count} attempts")
        return "exhausted"

    schedule = _schedule_for(row.get("outage_tier") or "retryable")
    delay = schedule[min(retry_count, len(schedule) - 1)]
    next_retry_at = (now + timedelta(minutes=delay)).isoformat()
    supabase_update("ai_response_retries", {"id": f"eq.{row['id']}"}, {
        "status": "pending", "retry_count": retry_count, "next_retry_at": next_retry_at,
        "last_error": (error or "")[:500], "updated_at": now.isoformat(),
    })
    logger.info(f"[ProviderRetry] Rescheduled ticket={row.get('ticket_id')} attempt={retry_count} next_retry_at={next_retry_at}")
    return "rescheduled"


def already_responded(ticket: Dict[str, Any]) -> bool:
    """A human replied manually, or an earlier attempt already sent —
    reuses the exact fields the rest of the app already treats as the
    source of truth for "has this ticket been answered" (see
    v2_tickets.py's `if ticket.get("human_approved") or
    ticket.get("response_sent")` guard)."""
    return bool(
        ticket.get("human_approved")
        or ticket.get("response_sent")
        or ticket.get("email_sent")
    )
