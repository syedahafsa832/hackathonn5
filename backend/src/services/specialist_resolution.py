"""
Specialist Resolution Contract (PART 2/3 — internal specialist architecture)
=============================================================================
The minimal structured hand-off between a specialist's decision (what
should happen for the customer's CURRENT intent — never a previous action,
see return_actions_integration.py's duplicate-guard comments) and the one
shared Executable Action Gate that decides whether that decision may become
a real `actions` table row.

Specialists never write to the `actions` table directly. They return a
Resolution; only stage_resolution_action() below is allowed to call
ReturnActionsIntegration._create_action() — see that method's own docstring
for what actually executes (approve_action() in actions_service.py, which
dispatches purely on the stored action_type, never on anything here).
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# get_custom_policy_text() can return up to several full RAG chunks (Store
# Pages / FAQ Pages) joined together - never dumped into the short,
# merchant-facing "reason" a human reads in ~5-10 seconds. Bounded here for
# the dashboard's expandable "View policy evidence" section instead; the
# untruncated eligibility dict (which still holds the raw text) is never
# deleted from extracted_data, only not surfaced as the primary reason.
#
# Lives here (rather than in return_actions_integration.py, where it
# originated) so both the integration/adapter layer and any specialist
# module (return_specialist.py, and future refund/cancellation/exchange
# specialists) can share it without a circular import between them.
POLICY_EVIDENCE_MAX_CHARS = 800


def policy_evidence_excerpt(raw_policy_text: Optional[str]) -> Optional[str]:
    """Bound a possibly multi-document policy/RAG lookup to a reasonable
    excerpt. Returns None (never an empty-but-present blob) when there's
    nothing to show, so the frontend can fall back to a safe, honest
    "policy information was found and requires human review" message
    instead of rendering nothing or the full dump."""
    text = (raw_policy_text or "").strip()
    if not text:
        return None
    if len(text) <= POLICY_EVIDENCE_MAX_CHARS:
        return text
    return text[:POLICY_EVIDENCE_MAX_CHARS].rstrip() + "…"


@dataclass
class Resolution:
    """specialist's output — never a database row, never itself an
    executable action. `requested_action_type` is None whenever the
    specialist decided nothing should be executed (e.g. Return and Exchange
    today, per current product policy — always escalate to a human)."""
    resolution_type: str
    specialist: str  # "refund" | "return" | "exchange" | "cancellation" | "general_support"
    order_id: Optional[str]
    reasoning: str
    customer_facing_note: str
    eligible: Optional[bool] = None
    requested_action_type: Optional[str] = None
    # Context-only pointer to a related pending/executed action found by the
    # caller's duplicate-guard lookup — never authority for this Resolution.
    # See return_actions_integration.py's _find_active_action comments: a
    # previous action must never silently define the current intent.
    existing_action_ref: Optional[Dict[str, Any]] = None


# The ONLY table this system currently allows a Resolution to turn into an
# executable action from. Keyed by resolution_type -> the single action_type
# it may create. Absent from this table = that resolution_type may never
# create ANY executable action, regardless of what a caller passes in.
#
# Current CURRENT AUTOMATION POLICY (see PART 2/3 task spec):
#   Refund:       AI may stage a refund action — human approval required.
#   Cancellation: AI may stage a cancel_order action — human approval required.
#   Return:       ALWAYS escalate to human. NO executable action, ever —
#                 not refund, not cancel_order, as a substitute.
#   Exchange:     ALWAYS escalate to human (for now). NO executable action,
#                 ever — not refund, not cancel_order, not even "exchange"
#                 itself, until this policy is revisited.
#   General Support: no executable action unless an explicitly supported
#                 workflow is later introduced.
_ACTION_WHITELIST: Dict[str, str] = {
    "refund_eligible": "refund",
    "cancellation_eligible": "cancel_order",
}


class ExecutableActionRejected(Exception):
    """Raised when a Resolution requests an executable action its own
    resolution_type is not whitelisted to create. This should never fire
    from real customer traffic — every specialist call site is expected to
    only ever request an action type its own resolution_type allows. A
    raise here means a specialist tried to create an action outside its
    lane (e.g. a return/exchange resolution requesting a refund) — a
    programming-time contract violation to catch in tests/review, not a
    customer-facing failure path."""


async def stage_resolution_action(integration, resolution: Resolution, **create_action_kwargs) -> Optional[dict]:
    """The one shared boundary between a Resolution and a real `actions`
    table row.

        Resolution -> validate -> whitelist -> integration._create_action() -> actions table

    Returns None (no action attempted) when the Resolution itself requested
    none — this is the normal, expected outcome for Return and Exchange
    today, not an error path. Reuses the existing `_create_action` /
    actions_service.create_action / approve_action infrastructure unchanged
    — this is not a second action system, just a gate in front of the
    existing one.

    `integration` is the ReturnActionsIntegration instance whose
    `_create_action` should be called — passed explicitly (rather than
    imported as a singleton) so tests can patch `integration._create_action`
    exactly as they already do today and this gate transparently calls
    through to that same mock."""
    if resolution.requested_action_type is None:
        return None

    allowed_action_type = _ACTION_WHITELIST.get(resolution.resolution_type)
    if allowed_action_type != resolution.requested_action_type:
        raise ExecutableActionRejected(
            f"resolution_type={resolution.resolution_type!r} (specialist={resolution.specialist!r}) "
            f"is not whitelisted to create action_type={resolution.requested_action_type!r} "
            f"(allowed: {allowed_action_type!r})"
        )

    logger.info(
        f"[SpecialistResolution] Gate approved: resolution_type={resolution.resolution_type!r} "
        f"-> action_type={resolution.requested_action_type!r} (order #{resolution.order_id})"
    )
    return await integration._create_action(action_type=resolution.requested_action_type, **create_action_kwargs)
