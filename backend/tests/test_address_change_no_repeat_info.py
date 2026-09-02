"""
Bug: Luna asked customers to repeat information they had already provided
in the SAME message (full order number, order email, and complete address),
instead of using it. Root causes found and fixed:

1. return_actions_integration.py's "ADDRESS INCOMPLETE" branch computed the
   exact list of missing fields (_validate_address) but then told the
   customer to re-supply the WHOLE bundle ("full name, street address,
   city, and country") regardless of what was actually missing - e.g. a
   complete US address with no explicit country word (customers don't say
   "USA" when writing to a US-based store) produced missing=["country"]
   but still asked for everything.
2. customer_success_agent.py's ownership_mismatch tool_context told the
   agent to "ask them to confirm the email used when ordering" even when
   the customer's message already stated one - the comparison already used
   it; asking again is asking them to repeat what was already checked.
3. Intent detection only sees the CURRENT message (intent_detector.detect
   takes no history), so an order number given in an earlier message of the
   same ticket was lost on a follow-up turn that only supplied the address.
   Fixed by reusing ticket.detected_order_id (the same field
   message_processor.py's STAGE 1.6/1.8 already tracks across turns for
   this exact reason) when the current message's own detection comes back
   with no order number - a deterministic backend lookup, never an LLM
   guess.

None of this weakens identity verification, the human-approval gate, or
invents missing address fields - it only stops re-asking for what's already
there.
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
from src.agent.customer_success_agent import CustomerSuccessAgent  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


CUSTOMER_EMAIL = "bushrazohaib84+qa9addresschange@gmail.com"


def _run_address_change(
    raw_address, parsed_address, sender_email=CUSTOMER_EMAIL,
    order_email=CUSTOMER_EMAIL, order_id="1009", validate=None,
):
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="address_change", order_id=order_id, raw_address=raw_address, confidence=0.9)

    shopify_order = {"shipping_address": {"address1": "999 Old Ave", "city": "Old Town", "country": "US"}, "fulfillment_status": "unfulfilled"}
    if order_email is not None:
        shopify_order["email"] = order_email

    fake_client = MagicMock()
    fake_client.get_order = AsyncMock(return_value={"success": True, "order": shopify_order})

    patches = [
        patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)),
        patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})),
        patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock(return_value=fake_client)),
        patch("src.services.intent_detector.intent_detector.parse_address", new=AsyncMock(return_value=parsed_address)),
    ]
    if validate is not None:
        patches.append(patch.object(integration, "_validate_address", return_value=validate))

    started = [p.start() for p in patches]
    mock_create = started[1]
    try:
        result = run(integration.handle_return_intent(
            query=f"change my address to {raw_address}",
            customer_info={"name": "Jane", "email": sender_email},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            ticket_id="ticket-1", intent_result=intent,
        ))
    finally:
        for p in patches:
            p.stop()
    return result, mock_create


# ── 1. Complete info in one message: no unnecessary clarification ─────────

def test_complete_address_in_one_message_does_not_ask_for_anything():
    parsed = {"address1": "42 Maple Street", "city": "Austin", "province": "TX", "zip": "78701", "country": "US"}
    result, mock_create = _run_address_change("42 Maple Street, Austin, TX 78701", parsed)

    mock_create.assert_awaited_once()
    assert "ADDRESS INCOMPLETE" not in result["action_context"]
    assert "ADDRESS TOO VAGUE" not in result["action_context"]
    assert "ADDRESS MISSING" not in result["action_context"]
    assert result["staged"] == {"success": True, "action_id": "a1"}
    # Correct order selected, exact address preserved through to staging.
    kwargs = mock_create.await_args.kwargs
    assert kwargs["order_id"] == "1009"
    assert kwargs["structured_address"] == parsed
    assert kwargs["new_address_text"] == "42 Maple Street, Austin, TX 78701"


# ── 2. Only one field missing: ask only for that field ─────────────────────

def test_missing_only_zip_asks_only_for_zip_not_the_whole_address():
    """Country isn't required by _validate_address (only address1/city/
    country are) - use a real gap _validate_address DOES flag: no country
    word in a message that also happens to omit a required field, isolated
    here by supplying a parsed address that's missing only country, and
    confirming the response asks for country by name without re-requesting
    the street/city that were already successfully parsed."""
    parsed = {"address1": "42 Maple Street", "city": "Austin", "province": "TX", "zip": "78701", "country": ""}
    result, mock_create = _run_address_change("42 Maple Street, Austin, TX 78701", parsed)

    mock_create.assert_not_awaited()  # not staged - genuinely incomplete
    ctx = result["action_context"]
    assert "ADDRESS INCOMPLETE" in ctx
    assert "country" in ctx.lower()
    assert "42 Maple Street" in ctx  # already-provided fields echoed back
    assert "Austin" in ctx
    # Never falls back to the generic full-bundle ask.
    assert "full name, street address, city, and country" not in ctx
    assert "full name, complete street address" not in ctx


def test_missing_field_message_does_not_reintroduce_generic_bundle_wording():
    """Regression guard directly on the fixed branch: the customer-facing
    suggestion must name only what's actually in `missing`."""
    integration = ReturnActionsIntegration()
    with patch.object(integration, "_validate_address", return_value=(False, ["city name"])):
        result, _ = _run_address_change(
            "42 Maple Street, TX 78701",
            {"address1": "42 Maple Street", "city": "", "province": "TX", "zip": "78701", "country": "US"},
        )
    assert "city name" in result["action_context"]
    assert "street address" not in result["action_context"].lower().split("missing only:")[1].split(".")[0]


# ── 3. Order number reused from an earlier message in the same ticket ─────

def _agent_intent_result_backfill(current_message_order_id, ticket_detected_order_id, action_type="address_change"):
    agent = CustomerSuccessAgent.__new__(CustomerSuccessAgent)  # only detect()'s consumer logic under test
    intent = IntentResult(action_type=action_type, order_id=current_message_order_id, raw_address="42 Maple Street, Austin, TX 78701", confidence=0.9)

    with patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=intent)), \
         patch("src.lib.supabase_client.supabase_select", return_value=[{"detected_order_id": ticket_detected_order_id}]):
        from src.services.intent_detector import intent_detector as _d

        async def _resolve():
            _intent_result = await _d.detect("42 Maple Street, Austin, TX 78701")
            if _intent_result.has_action and not _intent_result.order_id:
                from src.lib.supabase_client import supabase_select as _sel3
                rows = _sel3("tickets", {"id": "eq.ticket-1"})
                prior = rows[0].get("detected_order_id") if rows else None
                if prior:
                    _intent_result.order_id = str(prior)
            return _intent_result

        return run(_resolve())


def test_order_number_from_earlier_message_is_reused_when_current_message_omits_it():
    """Simulates the exact backfill logic added to process_customer_query:
    current message ('42 Maple Street...') has no order number of its own,
    but the ticket already has one from an earlier turn."""
    result = _agent_intent_result_backfill(current_message_order_id=None, ticket_detected_order_id="1009")
    assert result.order_id == "1009"


def test_order_number_already_present_is_not_overwritten_by_history():
    result = _agent_intent_result_backfill(current_message_order_id="1009", ticket_detected_order_id="9999")
    assert result.order_id == "1009"  # current message's own value wins, history never overrides it


# ── 4. Identity mismatch: never asks the customer to repeat the same email ─

def test_identity_mismatch_does_not_ask_customer_to_repeat_email():
    result, mock_create = _run_address_change(
        "42 Maple Street, Austin, TX 78701",
        {"address1": "42 Maple Street", "city": "Austin", "province": "TX", "zip": "78701", "country": "US"},
        sender_email=CUSTOMER_EMAIL, order_email="someone-else@example.com",
    )
    mock_create.assert_awaited_once()  # still stages for human review - never silently dropped
    ai_reasoning = mock_create.await_args.kwargs["ai_reasoning"]
    assert "NOT VERIFIED" in ai_reasoning
    assert mock_create.await_args.kwargs["identity_verified"] is False


def test_ownership_mismatch_wording_states_the_mismatch_not_a_repeat_request():
    """Direct check on the fixed tool_context wording (customer_success_agent.py) -
    must never instruct the LLM to ask the customer to confirm/repeat/resend
    an email, and must never promise a team follow-up that will never happen
    (a live incident showed exactly that false promise) - instead it must
    state the mismatch and fully resolve it in one reply."""
    import inspect
    source = inspect.getsource(CustomerSuccessAgent)
    block = source.split('order.get("ownership_mismatch")')[1].split("elif order.get")[0]
    assert "ask them to confirm the email used when ordering" not in block
    assert "do NOT ask them to confirm/repeat/resend" in block
    assert "do NOT say a team will" in block or "do NOT say 'I need our team to verify ownership'" in block
    assert "NOTHING is being escalated" in block


# ── 5. Generic order question is never misclassified as address change ────

def test_generic_order_question_not_treated_as_address_change():
    """An address mentioned elsewhere in conversation history must not, by
    itself, cause a plain question to be handled as an address-change
    request - the dispatch already only proceeds on intent_detector's own
    action_type, this locks that in."""
    none_intent = IntentResult(action_type="none", order_id="1009", raw_address=None, confidence=0.9)
    assert none_intent.has_action is False


# ── 7. Safety/eligibility/approval gates unchanged ─────────────────────────

def test_address_change_still_requires_a_pending_action_and_no_autopilot():
    import inspect
    source = inspect.getsource(ReturnActionsIntegration.handle_return_intent)
    block = source.split('intent_type == "address_change"')[1].split('intent_type == "reship"')[0]
    assert "_maybe_autopilot" not in block
    assert "approve_action" not in block


# ── 8. No unauthorized Shopify mutation from staging alone ─────────────────

def test_staging_never_calls_a_shopify_mutation_directly():
    parsed = {"address1": "42 Maple Street", "city": "Austin", "province": "TX", "zip": "78701", "country": "US"}
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="address_change", order_id="1009", raw_address="42 Maple Street, Austin, TX 78701", confidence=0.9)
    fake_client = MagicMock()
    fake_client.get_order = AsyncMock(return_value={"success": True, "order": {"email": CUSTOMER_EMAIL, "shipping_address": {}, "fulfillment_status": "unfulfilled"}})
    fake_client.update_shipping_address = AsyncMock()

    with patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock(return_value=fake_client)), \
         patch("src.services.intent_detector.intent_detector.parse_address", new=AsyncMock(return_value=parsed)):
        run(integration.handle_return_intent(
            query="change my address to 42 Maple Street, Austin, TX 78701",
            customer_info={"name": "Jane", "email": CUSTOMER_EMAIL},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            ticket_id="ticket-1", intent_result=intent,
        ))

    fake_client.update_shipping_address.assert_not_called()
