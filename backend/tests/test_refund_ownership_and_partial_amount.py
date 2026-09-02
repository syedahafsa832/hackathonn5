"""
Two related, confirmed-live production bugs in the refund workflow:

1. OWNERSHIP MISMATCH: a customer requests a refund from an email that does
   not match the order's Shopify customer email. `check_return_eligibility`
   flagged this with `staging_required`/`requires_manual_review` (the same
   flags a legitimate "needs a human look" case uses), so
   return_actions_integration.py went ahead and STAGED A REAL REFUND/CANCEL
   ACTION for an order the requester doesn't own — confirmed live: ticket
   11697c96 (order #1004, "sender email does not match order email on
   file") produced action eac083b7-cf01-49fd-aa04-2d783f44c94f, which
   reached status="executed". A second occurrence (ticket 616f990e, order
   #1010) went through the UNFULFILLED/cancel_order branch instead and also
   reached "executed". Both are real financial actions taken on someone
   else's order from an unverified identity, and Luna's reply on top
   promised "our verification team... will reach out to you shortly" /
   "you'll receive an email confirmation" when no such follow-up would ever
   happen.

   Fix: `check_return_eligibility`'s email-mismatch branch no longer sets
   staging_required/requires_manual_review (both actions_service.py's
   detect_and_create and return_actions_integration.py's handle_return_intent
   gate action creation on those two flags) and no longer returns real order
   data. It sets a new `identity_mismatch` flag instead, which
   return_actions_integration.py routes to a dedicated response: no action,
   no escalation claim, one self-contained reply asking the customer to
   contact from the order's own email. customer_success_agent.py's parallel
   ownership_mismatch prompt path (triggered by a plain order-status lookup)
   gets the same treatment.

2. PARTIAL REFUND AMOUNT DROPPED: "I'd like a $5 refund for order #1001"
   never captured the $5 anywhere - the refund action always staged as a
   full-order refund, with the approval UI's partial-amount field always
   blank. A merchant clicking Approve without noticing the customer's own
   message would refund the FULL order, not $5.

   Fix: return_actions_integration.py now extracts a plainly-stated dollar
   figure (deterministic regex, never an LLM guess) and stores it as
   extracted_data.requested_amount on the staged action. The dashboard's
   partial-refund field now pre-fills from this value instead of always
   starting blank. Execution is untouched: Shopify still only ever receives
   whatever amount the human submits at approval time (override_amount) -
   this fix only changes what that field defaults to, never who authorizes
   the number that reaches Shopify.

FOLLOW-UP: two gaps found by a stricter invariant check on the above fix.

3. FAKE ESCALATION SURVIVED THE IDENTITY-MISMATCH FIX: clearing `escalate`
   still wasn't enough - real production data for this exact scenario
   (ticket 01b2c6cb..., order #1009) shows the model rated the turn
   risk_level="high", and the existing identity-verification backstop
   (_enforce_no_escalation_for_safe_identity_verification_response) only
   clears escalate when risk_level != "high" - by design, so a genuinely
   high-risk signal is never weakened. That guard also depends on a
   SEPARATE detection path (the plain order-status keyword lookup) that
   only fires when the message happens to contain "order"/"shipped"/etc.
   Fixed with a new, narrower backstop
   (_enforce_no_escalation_for_identity_mismatch) keyed off
   return_actions_integration.py's own identity_mismatch signal (LLM
   intent-classified, not keyword-matched) that deliberately does NOT gate
   on risk_level, because identity_mismatch is a verified fact (a live
   Shopify email comparison) that nothing was staged - never a general
   weakening of high-risk handling elsewhere. message_processor.py's
   _decide_ticket_routing gets a matching identity_mismatch parameter that
   short-circuits its own risk-driven fallback for this one case only.

4. SECOND LIVE APPROVAL SURFACE NEVER GOT THE PARTIAL-REFUND PREFILL:
   dashboard/src/components/ActionCard.jsx (rendered by Dashboard.jsx and
   TicketDetail.jsx, posting to /api/v2/actions/{id}/approve) is a
   genuinely separate component from Actions.jsx's own inline ActionCard -
   only the latter was fixed. Approving a stated "$5 refund" from the
   Ticket Detail page would show a blank field and silently execute a full
   refund. Fixed the same way: pre-fill from
   action.extracted_data.requested_amount. No dashboard test runner exists
   in this repo (no test script, no vitest/jest/@testing-library in
   package.json) - verified by code inspection, plus a backend-level test
   proving the exact payload this component now submits by default reaches
   Shopify as that amount, and that requested_amount is never itself an
   implicit fallback (the backend still requires an explicit submitted
   amount - the frontend fix is not the only thing standing between a
   customer's ask and what Shopify executes).
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402

from src.services.actions_manager import actions_manager  # noqa: E402
from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402
from src.agent.customer_success_agent import (  # noqa: E402
    _enforce_no_escalation_for_identity_mismatch,
)
from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


_ORDER_OWNER_EMAIL = "customer@example.com"
_WRONG_EMAIL = "attacker@example.com"
_ORDER = "1009"


def _shopify_order(**overrides):
    order = {
        "email": _ORDER_OWNER_EMAIL,
        "total_price": "100.00",
        "refunds": [],
        "cancelled_at": None,
        "fulfillment_status": "fulfilled",
    }
    order.update(overrides)
    return order


# ── check_return_eligibility: identity mismatch is a hard block ───────────

def test_email_mismatch_is_never_staging_required_and_leaks_no_order_data():
    with patch.object(actions_manager, "_get_order_from_shopify", new=AsyncMock(return_value=_shopify_order())):
        result = _run(actions_manager.check_return_eligibility(_ORDER, _WRONG_EMAIL))

    assert result["eligible"] is False
    assert result["identity_mismatch"] is True
    assert result["staging_required"] is False
    assert result["requires_manual_review"] is False
    assert result["order"] is None
    assert result["items"] == []


def test_matching_email_is_unaffected_by_the_identity_mismatch_change():
    with patch.object(actions_manager, "_get_order_from_shopify", new=AsyncMock(return_value=_shopify_order())):
        result = _run(actions_manager.check_return_eligibility(_ORDER, _ORDER_OWNER_EMAIL))

    assert "identity_mismatch" not in result
    assert result["order"] is not None  # legitimate request still gets real order data


# ── handle_return_intent: Test 1 — wrong email refund ──────────────────────

def _run_refund(order_id, email, eligibility, query=None, ticket_id="ticket-1"):
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="refund", order_id=order_id, raw_address=None, confidence=0.9)
    query = query or f"Hi Luna, I'd like a refund for order #{order_id}. Can you check if I'm eligible?"

    create_mock = AsyncMock(return_value={"success": True, "action_id": "refund-1"})
    with patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_create_action", new=create_mock), \
         patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration, "_maybe_autopilot_refund", new=AsyncMock(return_value=None)):
        result = _run(integration.handle_return_intent(
            query=query, customer_info={"name": "Customer", "email": email},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            ticket_id=ticket_id, intent_result=intent,
        ))
    return result, create_mock


def test_wrong_email_refund_creates_no_action_and_no_escalation_claim():
    eligibility = {
        "eligible": False, "eligibility_verified": False,
        "reason": "sender email does not match order email on file",
        "order": None, "items": [], "requires_manual_review": False,
        "staging_required": False, "identity_mismatch": True,
    }
    result, create_mock = _run_refund(_ORDER, _WRONG_EMAIL, eligibility)

    create_mock.assert_not_awaited()
    assert "staged" not in result
    text = result["action_context"]
    assert "IDENTITY UNVERIFIED" in text
    # Never the "we've submitted/staged this" templates a real action would get.
    assert "REQUEST SUBMITTED FOR MANUAL REVIEW" not in text
    assert "ACTION STAGED FOR APPROVAL" not in text
    # The customer-facing instruction resolves this in one message, not a
    # promised follow-up nothing backs.
    assert "do not say this has been escalated" in text.lower()
    assert "reach out to us from the email address used when placing the order" in text.lower()


# ── Test 5 — wrong order / mismatched ownership can't cause a real refund ──

def test_identity_mismatch_never_creates_an_action_against_the_requested_order():
    eligibility = {
        "eligible": False, "identity_mismatch": True, "order": None, "items": [],
        "requires_manual_review": False, "staging_required": False,
        "reason": "sender email does not match order email on file",
    }
    _, create_mock = _run_refund(_ORDER, _WRONG_EMAIL, eligibility)
    create_mock.assert_not_awaited()  # no action of ANY type/order was created


# ── Test 2 — correct email refund stages a real, human-approval-gated action ─

def test_correct_email_refund_still_creates_action_requiring_approval():
    eligibility = {
        "eligible": True, "order": {"fulfillment_status": "fulfilled"},
        "items": [{"title": "Item"}],
    }
    result, create_mock = _run_refund(_ORDER, _ORDER_OWNER_EMAIL, eligibility)

    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["action_type"] == "refund"
    assert create_mock.await_args.kwargs["order_id"] == _ORDER
    assert result["staged"]["success"] is True
    assert "ACTION STAGED FOR APPROVAL" in result["action_context"]


# ── Test 3 — partial refund amount is preserved through staging ───────────

def test_partial_refund_amount_is_extracted_and_attached_to_the_action():
    eligibility = {
        "eligible": True, "order": {"fulfillment_status": "fulfilled"},
        "items": [{"title": "Item"}],
    }
    result, create_mock = _run_refund(
        "1001", _ORDER_OWNER_EMAIL, eligibility,
        query="Hi Luna, I'd like a $5 refund for order #1001.",
    )

    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["requested_amount"] == 5.0
    assert result["staged"]["success"] is True


# ── Test 4 — no amount requested preserves existing full-refund behavior ──

def test_no_amount_mentioned_preserves_full_refund_behavior():
    eligibility = {
        "eligible": True, "order": {"fulfillment_status": "fulfilled"},
        "items": [{"title": "Item"}],
    }
    result, create_mock = _run_refund(
        "1001", _ORDER_OWNER_EMAIL, eligibility,
        query="Hi Luna, I'd like a refund for order #1001, please.",
    )

    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["requested_amount"] is None
    assert result["staged"]["success"] is True


# ── Test 6 — duplicate-action guard is not bypassed by a dollar mention ───

def test_repeat_refund_with_a_dollar_amount_still_hits_the_existing_duplicate_guard():
    existing = {"id": "existing-1", "order_id": "1001", "action_type": "refund", "status": "pending"}
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="refund", order_id="1001", raw_address=None, confidence=0.9)

    create_mock = AsyncMock()
    with patch.object(integration, "_find_active_action", new=AsyncMock(return_value=existing)), \
         patch.object(integration, "_create_action", new=create_mock):
        result = _run(integration.handle_return_intent(
            query="Just checking again — can you make sure my $5 refund for #1001 goes through?",
            customer_info={"name": "Customer", "email": _ORDER_OWNER_EMAIL},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            ticket_id="ticket-1", intent_result=intent,
        ))

    create_mock.assert_not_awaited()  # the pre-existing dedup guard still wins — no second/duplicate action
    assert "duplicate_of_existing_action" in result


# ── ownership_mismatch prompt text (customer_success_agent.py's parallel path) ─

def test_ownership_mismatch_prompt_never_promises_a_team_follow_up():
    """This tool_context branch sits deep inside one large async method with
    an LLM call, tool_results, and progress-emit plumbing around it — not
    reasonably unit-testable in isolation without mocking most of that
    machinery for a pure string-content check. A direct source check is the
    smallest regression guard that the specific broken promise this bug
    shipped with cannot silently come back, and that its replacement is in
    place."""
    import inspect
    import src.agent.customer_success_agent as csa
    src_text = inspect.getsource(csa)
    # The exact old, now-removed promise this bug shipped with.
    assert "I need our team to verify ownership before I can make any changes." not in src_text
    assert "do NOT say a team will" in src_text
    assert "reach out to us from the email address used when placing the order" in src_text


# ── GAP 1 FIX: identity mismatch must never end in a fake escalation ──────
# even when the model rates the turn risk_level="high", and regardless of
# whether the message contains the literal word "order".

_MISMATCH_REPLY = (
    "I found this order, but the email you're contacting us from doesn't match the one on the "
    "order. For your security I'm not able to share order details or process a refund from this "
    "email. Could you reach out to us from the email address used when this order was placed?"
)


def test_identity_mismatch_backstop_clears_escalation_even_at_high_risk():
    """Unlike every other backstop in this file, this one must NOT respect
    risk_level=="high" — that guard is what let the original bug survive
    (real production ticket 01b2c6cb..., risk_level="high") even after
    escalate was otherwise cleared."""
    structured = {
        "reply_body": _MISMATCH_REPLY, "status": "escalated", "escalate": True,
        "risk_level": "high", "confidence_score": 65,
    }
    result = _enforce_no_escalation_for_identity_mismatch(structured, True)
    assert result["escalate"] is False
    assert result["status"] == "auto_resolved"
    assert result["identity_mismatch"] is True


def test_no_identity_mismatch_leaves_structured_untouched():
    structured = {"reply_body": _MISMATCH_REPLY, "status": "escalated", "escalate": True, "risk_level": "high"}
    result = _enforce_no_escalation_for_identity_mismatch(structured, False)
    assert result["escalate"] is True
    assert result["status"] == "escalated"
    assert "identity_mismatch" not in result


def test_failed_generation_is_never_overridden_into_a_fake_handled_state_for_identity_mismatch():
    structured = {"reply_body": "", "status": "escalated", "escalate": True, "risk_level": "high"}
    result = _enforce_no_escalation_for_identity_mismatch(structured, True)
    assert result["escalate"] is True
    assert result["status"] == "escalated"


def test_routing_resolves_identity_mismatch_at_high_risk_instead_of_escalating():
    """Routing-layer half of the same fix: even with risk_level="high" and
    confidence below the auto-reply threshold, identity_mismatch=True must
    short-circuit straight to a resolved, sent reply — never "escalated"."""
    proc = UnifiedMessageProcessor()
    routing = proc._decide_ticket_routing(
        ai_mode="active", is_overridden=False, confidence=0.65, confidence_threshold=0.65,
        ai_flagged_escalate=False, risk_level="high", reply_body=_MISMATCH_REPLY,
        identity_mismatch=True,
    )
    assert routing["should_auto_reply"] is True
    assert routing["status"] == "auto_resolved"


def test_routing_without_identity_mismatch_reproduces_the_original_bug():
    """Sanity check this is real: the exact same inputs, without the new
    parameter (what every caller did before this fix), land on the
    reported contradiction — a fully answerable request stuck at
    "escalated" purely because of risk_level."""
    proc = UnifiedMessageProcessor()
    routing = proc._decide_ticket_routing(
        ai_mode="active", is_overridden=False, confidence=0.65, confidence_threshold=0.65,
        ai_flagged_escalate=False, risk_level="high", reply_body=_MISMATCH_REPLY,
    )
    assert routing["status"] == "escalated"


def test_human_takeover_still_wins_over_identity_mismatch():
    """A human already managing this conversation must not be silently
    overridden by the deterministic identity-mismatch auto-reply."""
    proc = UnifiedMessageProcessor()
    routing = proc._decide_ticket_routing(
        ai_mode="active", is_overridden=True, confidence=0.65, confidence_threshold=0.65,
        ai_flagged_escalate=False, risk_level="high", reply_body=_MISMATCH_REPLY,
        identity_mismatch=True,
    )
    assert routing["status"] == "human_managing"


def test_identity_mismatch_end_to_end_no_order_keyword_no_action_no_escalation_no_false_promise():
    """The exact regression requested: identity mismatch, risk_level="high",
    a message that never says the word "order", no action created, final
    escalate=False, final status not escalated, and no false team-follow-up
    claim anywhere in what would be sent."""
    # 1. return_actions_integration.py's own detection — LLM intent-
    #    classified (order_id comes from IntentResult, not a keyword scan),
    #    so this fires the same way with or without the word "order".
    eligibility = {
        "eligible": False, "identity_mismatch": True, "order": None, "items": [],
        "requires_manual_review": False, "staging_required": False,
        "reason": "sender email does not match order email on file",
    }
    result, create_mock = _run_refund(
        _ORDER, _WRONG_EMAIL, eligibility,
        query="please check #1009 refund eligibility for me",  # no "order" anywhere
    )
    create_mock.assert_not_awaited()  # no action exists
    assert "staged" not in result
    action_context = result["action_context"]
    assert "do not say this has been escalated" in action_context.lower()

    # 2. The two backstops that turn that finding into a final, non-escalated
    #    ticket state, exactly as customer_success_agent.py /
    #    message_processor.py apply them — simulating the model
    #    independently rating this turn "high" risk, as real production
    #    data for this exact scenario showed.
    structured = {
        "reply_body": _MISMATCH_REPLY, "status": "escalated", "escalate": True,
        "risk_level": "high", "confidence_score": 65,
    }
    structured = _enforce_no_escalation_for_identity_mismatch(structured, True)
    assert structured["escalate"] is False

    proc = UnifiedMessageProcessor()
    routing = proc._decide_ticket_routing(
        ai_mode="active", is_overridden=False, confidence=0.65, confidence_threshold=0.65,
        ai_flagged_escalate=structured["escalate"], risk_level="high", reply_body=_MISMATCH_REPLY,
        identity_mismatch=structured.get("identity_mismatch", False),
    )
    assert routing["status"] != "escalated"
    assert routing["status"] == "auto_resolved"
    assert routing["should_auto_reply"] is True
    assert "will reach out" not in routing["ai_reply"].lower()
    assert "our team is" not in routing["ai_reply"].lower()


# ── GAP 2 FIX: the second live approval surface (ActionCard.jsx) ──────────
# No JS test runner exists in this repo (no test script, no vitest/jest/
# @testing-library in dashboard/package.json) - dashboard/src/components/
# ActionCard.jsx's prefill itself was verified by direct code inspection
# (mirrors Actions.jsx's own fix exactly: `useState(requestedAmount != null
# ? String(requestedAmount) : '')`, and its handleApprove already submits
# `{amount: parsed}` whenever the field is non-empty - unchanged logic,
# now just seeded with a non-empty default). These are the smallest
# backend/API tests proving the exact payload that submission produces
# reaches Shopify as that amount, and that the new field is never itself
# an implicit backend fallback.

def _v2_approve(order_extracted_data, body):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.routes.v2_actions import router as v2_actions_router
    from src.api.middleware.auth_middleware import get_current_user, UserContext, AuthenticatedContext, UserRole

    app = FastAPI()
    app.include_router(v2_actions_router, prefix="/api/v2")
    test_client = TestClient(app)
    brand_id = "brand-1"

    def own_context():
        return AuthenticatedContext(
            user=UserContext(user_id="user-1", supabase_auth_id="auth-1",
                              organization_id="org-1", email="owner@example.com",
                              role=UserRole.ADMIN, brands=[brand_id]),
            organization=None, brand_ids=[brand_id],
        )

    def fake_select(table, params=None):
        if table == "actions":
            return [{
                "id": "action-1", "brand_id": brand_id, "status": "pending",
                "action_type": "refund", "order_id": "1001",
                "extracted_data": order_extracted_data,
            }]
        if table == "brands":
            return [{"id": brand_id, "shopify_connected": True, "shopify_domain": "x.myshopify.com", "shopify_access_token": "enc"}]
        return []

    captured = {}

    async def fake_process_refund(self, order_id, amount=None, reason=None, restock=True, notify_customer=False, **kwargs):
        captured["amount"] = amount
        return {"success": True, "refund_id": 999, "message": "ok"}

    app.dependency_overrides[get_current_user] = own_context
    try:
        with patch("src.api.routes.v2_actions.supabase_select", side_effect=fake_select), \
             patch("src.api.routes.v2_actions.supabase_update", return_value={"id": "action-1"}), \
             patch("src.api.routes.v2_actions.supabase_insert", return_value={}), \
             patch("src.services.shopify_service.decrypt_token", return_value="tok"), \
             patch("src.services.shopify_service.ShopifyClient.process_refund", new=fake_process_refund), \
             patch("src.services.actions_service.actions_service._post_execution_notify", new=AsyncMock()):
            resp = test_client.post("/api/v2/actions/action-1/approve", json=body)
    finally:
        app.dependency_overrides.clear()

    return resp, captured


def test_actioncard_default_submission_reaches_shopify_as_the_requested_amount():
    """The exact ActionCard.jsx path: extracted_data.requested_amount=5.0
    (customer said "$5 refund" for a $15 order), and the field's now-
    prefilled default value (5.0) is what gets submitted on a plain
    Approve click. $5 must reach Shopify — never the $15 order total."""
    resp, captured = _v2_approve(
        {"requested_amount": 5.0, "order_total": 15.0},
        body={"amount": 5.0},
    )
    assert resp.status_code == 200
    assert captured["amount"] == 5.0


def test_requested_amount_alone_is_never_an_implicit_backend_fallback():
    """Safety net independent of the frontend fix: extracted_data.
    requested_amount is display/prefill-only. If an approval request ever
    omits `amount` entirely (bypassing the UI's default), the backend must
    NOT silently pick it up on its own — the pre-existing full-refund
    default is what still applies, exactly as before this feature existed."""
    resp, captured = _v2_approve(
        {"requested_amount": 5.0, "order_total": 15.0},
        body={},
    )
    assert resp.status_code == 200
    assert captured["amount"] is None  # process_refund's own full-refund default — not 5.0, not 15.0
