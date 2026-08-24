"""
Policy Evidence & Deterministic Decision Layer
===============================================
Narrow, additive verification for the ONE class of policy that was
previously never verified deterministically: a free-text time-window
condition (e.g. "orders can be cancelled within 2 hours of placing the
order"). Everything else about how policies are retrieved, staged, or
escalated is unchanged - this module only decides, for that one shape of
policy, whether the customer's ACTUAL order satisfies it.

RAG (brand_knowledge_service / get_custom_policy_text) answers "what does
the merchant's policy say?" This module answers "does this customer's real
order satisfy that policy?" - using only the already-fetched, authoritative
Shopify order timestamp, never the customer's wording, the email's
received time, or a model-generated date. The LLM is never asked to decide
this; it only explains an already-computed result (see action_context
strings built by callers).

Every result is one of:
  ELIGIBLE   - a confident regex match found a time window, real order
               timestamp was available, and the order is within it.
  INELIGIBLE - same as above, but the window has passed.
  UNKNOWN    - the policy text didn't contain a confidently-parseable
               window, or the order timestamp couldn't be read. Callers
               MUST treat UNKNOWN as "cannot verify" (falls back to the
               existing escalate-for-human-review behavior), never as an
               automatic yes.

Deliberately NOT a general rules engine: it recognizes exactly one pattern
family (a number of hours/days/weeks near a cancel/refund/return keyword)
and returns UNKNOWN for anything else, by design.
"""
import re
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_UNIT_TO_HOURS = {"hour": 1.0, "hr": 1.0, "hrs": 1.0, "day": 24.0, "week": 24.0 * 7}
_UNIT_PATTERN = "hour|hr|hrs|day|week"


def _build_patterns(keywords: List[str]) -> List["re.Pattern"]:
    kw = "|".join(re.escape(k) for k in keywords)
    # Three phrasings, all requiring the number/unit and the keyword to sit
    # in the same sentence (bounded, no '.' crossing) so an unrelated
    # "ships within 2 days" clause elsewhere in a multi-paragraph policy
    # document can't be mistaken for a cancellation/refund window.
    return [
        # "Cancellations ... within 2 hours"
        re.compile(rf"\b(?:{kw})\w*\b[^.]{{0,60}}?\bwithin\s+(\d+(?:\.\d+)?)\s*({_UNIT_PATTERN})s?\b", re.IGNORECASE),
        # "within 2 hours ... of cancellation" / "... you can cancel"
        re.compile(rf"\bwithin\s+(\d+(?:\.\d+)?)\s*({_UNIT_PATTERN})s?\b[^.]{{0,60}}?\b(?:{kw})\w*\b", re.IGNORECASE),
        # "You have 2 hours to cancel"
        re.compile(rf"\b(\d+(?:\.\d+)?)\s*({_UNIT_PATTERN})s?\b[^.]{{0,40}}?\bto\s+(?:{kw})\w*\b", re.IGNORECASE),
    ]


def _extract_window_hours(policy_text: str, keywords: List[str]) -> Optional[float]:
    if not policy_text:
        return None
    for pattern in _build_patterns(keywords):
        m = pattern.search(policy_text)
        if m:
            try:
                value = float(m.group(1))
            except (TypeError, ValueError):
                continue
            unit = m.group(2).lower()
            return value * _UNIT_TO_HOURS.get(unit, 1.0)
    return None


def verify_time_window(
    policy_text: Optional[str],
    order_created_at: Optional[str],
    keywords: List[str],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Deterministically verify a free-text time-window policy against the
    order's real Shopify creation timestamp. `now` is only ever overridden
    in tests - production always uses the real current time, never a value
    derived from the customer's message or the ticket/email timestamps.

    Returns {"status": "ELIGIBLE"|"INELIGIBLE"|"UNKNOWN", "reason": str,
    "evidence": {...}} - evidence always includes what was actually checked
    (or None where it couldn't be determined), for logging/audit, never a
    fabricated number."""
    window_hours = _extract_window_hours(policy_text or "", keywords)
    evidence: Dict[str, Any] = {
        "policy_window_hours": window_hours,
        "order_created_at": order_created_at,
        "current_time": None,
        "elapsed_hours": None,
    }
    if window_hours is None:
        return {"status": "UNKNOWN", "reason": "policy_not_parseable", "evidence": evidence}
    if not order_created_at:
        return {"status": "UNKNOWN", "reason": "missing_order_created_at", "evidence": evidence}

    try:
        order_dt = datetime.fromisoformat(str(order_created_at).replace("Z", "+00:00"))
        if order_dt.tzinfo is None:
            order_dt = order_dt.replace(tzinfo=timezone.utc)
        current_dt = now if now is not None else datetime.now(order_dt.tzinfo)
        if current_dt.tzinfo is None:
            current_dt = current_dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError) as e:
        logger.warning(f"[PolicyEvidence] Could not parse order_created_at={order_created_at!r}: {e}")
        return {"status": "UNKNOWN", "reason": "invalid_order_timestamp", "evidence": evidence}

    elapsed_hours = (current_dt - order_dt).total_seconds() / 3600.0
    evidence["current_time"] = current_dt.isoformat()
    evidence["elapsed_hours"] = round(elapsed_hours, 4)

    # Strict less-than: a window of "2 hours" means the deadline itself
    # (elapsed == exactly 2h) is no longer within it.
    if elapsed_hours < window_hours:
        return {"status": "ELIGIBLE", "reason": "within_window", "evidence": evidence}
    return {"status": "INELIGIBLE", "reason": "window_expired", "evidence": evidence}
