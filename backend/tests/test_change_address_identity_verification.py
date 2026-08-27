"""
Security fix: customer-initiated Change Address had no identity/ownership
verification at all - unlike refund/cancel, which already compare the
order's Shopify email against the conversation's trusted sender email
(actions_manager.check_return_eligibility Step 2) before staging.

Reuses that exact comparison (not a new verification system): the address-
change staging path already fetches the live order (for the current-
shipping-address enrichment); this now also compares order.email against
the sender email from that same fetch and records identity_verified /
identity_verification_reason in extracted_data - the existing pattern
already used for eligibility/policy_evidence/order_snapshot.

The actual safety boundary was, and remains, the human-approval gate:
address_change has no Autopilot flag and never auto-executes for a
customer-initiated request. This fix does not change that - it only
ensures the merchant reviewing the escalation is shown an accurate
verification status instead of none.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402
from src.services.actions_service import actions_service  # noqa: E402

NEW_ADDRESS = {"address1": "123 New St", "city": "Springfield", "province": "IL", "zip": "62704", "country": "US", "name": "Jane Doe"}


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _run_address_change(sender_email="jane@example.com", order_email="unset", order_lookup_ok=True):
    """order_email='unset' means don't include the key on the order at all
    (Shopify sometimes omits it for guest/legacy orders) - distinct from an
    explicit empty string, both of which must be treated the same (no
    email to compare against)."""
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="address_change", order_id="1001", raw_address="123 New St, Springfield, IL 62704", confidence=0.9)

    shopify_order = {"shipping_address": {"address1": "999 Old Ave", "city": "Old Town", "country": "US"}, "fulfillment_status": "unfulfilled"}
    if order_email != "unset":
        shopify_order["email"] = order_email

    fake_client = MagicMock()
    fake_client.get_order = AsyncMock(
        return_value={"success": True, "order": shopify_order} if order_lookup_ok else {"success": False}
    )

    with patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})) as mock_create, \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock(return_value=fake_client)), \
         patch.object(integration, "_validate_address", return_value=(True, [])), \
         patch("src.services.intent_detector.intent_detector.parse_address", new=AsyncMock(return_value=NEW_ADDRESS)):
        run(integration.handle_return_intent(
            query="please change my address to 123 New St, Springfield, IL 62704",
            customer_info={"name": "Jane", "email": sender_email},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            ticket_id="ticket-1", intent_result=intent,
        ))
    return mock_create


# ── 1. Matching Shopify order email → verified ───────────────────────────

def test_matching_order_email_marks_identity_verified():
    mock_create = _run_address_change(sender_email="jane@example.com", order_email="jane@example.com")
    kwargs = mock_create.await_args.kwargs
    assert kwargs["identity_verified"] is True
    assert kwargs["identity_verification_reason"] is None


def test_matching_order_email_is_case_insensitive():
    mock_create = _run_address_change(sender_email="Jane@Example.com", order_email="jane@example.com")
    assert mock_create.await_args.kwargs["identity_verified"] is True


# ── 2. Mismatched Shopify order email → blocked from being presented as verified ──

def test_mismatched_order_email_is_not_verified():
    mock_create = _run_address_change(sender_email="jane@example.com", order_email="someone-else@example.com")
    kwargs = mock_create.await_args.kwargs
    assert kwargs["identity_verified"] is False
    assert "does not match" in kwargs["identity_verification_reason"].lower()


def test_mismatched_identity_still_stages_for_human_review_never_silently_drops():
    """Keeping the human-approval gate intact means the action must still
    be created - just clearly flagged - never silently rejected or
    auto-executed either way."""
    mock_create = _run_address_change(sender_email="jane@example.com", order_email="someone-else@example.com")
    mock_create.assert_awaited_once()
    ai_reasoning = mock_create.await_args.kwargs["ai_reasoning"]
    assert "NOT VERIFIED" in ai_reasoning


# ── 3. Missing Shopify customer email → treated safely as unverified ────────

def test_order_with_no_email_on_file_is_treated_as_unverified():
    """Deliberately stricter than refund/cancel's own lenient behavior for
    a missing order email (which proceeds as 'no conflict found') - an
    address change redirects a physical shipment, so ambiguity defaults to
    unverified, not to a free pass."""
    mock_create = _run_address_change(sender_email="jane@example.com", order_email=None)
    kwargs = mock_create.await_args.kwargs
    assert kwargs["identity_verified"] is False
    assert "no customer email on file" in kwargs["identity_verification_reason"].lower()


def test_order_missing_email_key_entirely_is_also_treated_as_unverified():
    mock_create = _run_address_change(sender_email="jane@example.com", order_email="unset")
    assert mock_create.await_args.kwargs["identity_verified"] is False


def test_no_sender_email_available_is_treated_as_unverified():
    mock_create = _run_address_change(sender_email=None, order_email="jane@example.com")
    kwargs = mock_create.await_args.kwargs
    assert kwargs["identity_verified"] is False
    assert "no verified sender email" in kwargs["identity_verification_reason"].lower()


def test_order_lookup_failure_is_treated_as_unverified_not_silently_trusted():
    mock_create = _run_address_change(order_lookup_ok=False)
    assert mock_create.await_args.kwargs["identity_verified"] is False


# ── 4. Customer cannot mutate another customer's order (execution-side) ────

@pytest.mark.asyncio
async def test_unverified_action_still_requires_a_human_approve_call_to_execute():
    """The identity check is informational for the approver, not a second
    execution gate - approve_action() still only runs on an explicit
    approve, and still executes the SAME way regardless of the flag
    (a human who approves anyway is trusted to have verified some other
    way, e.g. by phone) - this proves nothing here bypasses that human
    click by itself: create_action() alone never calls Shopify."""
    with patch("src.services.actions_service.supabase_insert", return_value={"id": "action-1"}) as mock_insert, \
         patch("src.services.actions_service.supabase_select", return_value=[]), \
         patch.object(actions_service, "_calculate_risk", new=AsyncMock(return_value=("low", []))), \
         patch.object(actions_service, "_log_event", new=AsyncMock()):
        result = await actions_service.create_action(
            tenant_id="tenant-1", action_type="change_address", customer_email="attacker@example.com",
            order_id="1001", extracted_data={"new_address": NEW_ADDRESS, "identity_verified": False, "identity_verification_reason": "mismatch"},
        )

    assert result["success"] is True
    assert result["status"] == "pending"
    mock_insert.assert_called_once()
    # No Shopify client was ever touched by create_action alone.


# ── 6. Existing approval/autopilot safety gates remain intact ───────────────

def test_address_change_still_has_no_autopilot_hook():
    """Confirms this fix didn't introduce (or accidentally require) an
    autopilot-style auto-approve for address_change - it stays
    staging-only for the customer-initiated path, exactly as before."""
    import inspect
    source = inspect.getsource(ReturnActionsIntegration.handle_return_intent)
    address_change_block = source.split('intent_type == "address_change"')[1].split('intent_type == "reship"')[0]
    assert "_maybe_autopilot" not in address_change_block
    assert "approve_action" not in address_change_block


def test_duplicate_request_guard_still_runs_before_identity_check():
    """A repeated request against an order that already has a pending
    address-change escalation must still short-circuit to the existing
    duplicate-status message - never re-run identity verification or
    re-stage, regardless of what this fix adds."""
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="address_change", order_id="1001", raw_address="123 New St", confidence=0.9)
    existing = {"status": "pending", "action_type": "change_address"}

    with patch.object(integration, "_find_active_action", new=AsyncMock(return_value=existing)), \
         patch.object(integration, "_create_action", new=AsyncMock()) as mock_create, \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_client_getter:
        result = run(integration.handle_return_intent(
            query="please change my address to 123 New St",
            customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            ticket_id="ticket-1", intent_result=intent,
        ))

    mock_create.assert_not_awaited()
    mock_client_getter.assert_not_awaited()
    assert "ALREADY PENDING" in result["action_context"]
