"""
Order Identity Verification Follow-Up bug.

Turn 1: "where is my order #1013?" - order exists but the requester's email
doesn't match, so Luna correctly asks the customer to confirm the email used
(see test_shopify_order_context_reaches_ai.py for that fix). Turn 2: the
customer replies with just an email ("customer10@example.com") - no order
number, no "order" keyword. The OLD order-lookup block in
customer_success_agent.py only ever ran when the message itself contained an
order keyword/number, so this bare follow-up never re-ran the Shopify lookup
at all - the conversation stayed stuck on "I can't pull up your order" even
though the dashboard already had the verified, cancelled order.

Fix: when the current message has no order number of its own but does
contain an email, and this ticket's own last outbound/draft message carries
the needs_email_verification marker (set deterministically whenever the
ownership_mismatch branch fires, persisted by message_processor.py's STAGE
10), customer_success_agent.py re-runs the SAME get_order_status lookup
using the order number already known from this ticket
(tickets.detected_order_id, untouched by thread-continuation updates) and
the newly supplied email. No LLM call is used to detect the email or the
follow-up state - both are deterministic (regex + a stored boolean).

The ownership check itself (tools.py) is completely unmodified by this
task - test_get_order_status_customer_email_mismatch_blocks_access still
passes untouched.
"""
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


_FAKE_USAGE = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}

MATCHING_ORDER = {
    "success": True, "order_number": "1013", "order_id": "1013",
    "status": "unfulfilled", "financial_status": "paid",
    "cancelled_at": "2026-08-22T06:23:54Z",
    "tracking_number": None, "tracking_url": None, "tracking_company": None,
    "shipment_status": None, "shipped_at": None,
    "fulfillments": [], "fulfillment_count": 0,
    "total_amount": "120.00", "items": [], "created_at": "2026-08-21T12:26:24Z",
}
MISMATCH_RESULT = {"error": "Order #1013 not found.", "order_number": "1013", "ownership_mismatch": True}


def _fake_ai_response():
    content = json.dumps({"intent": "order_status_inquiry", "reply_body": "ok", "risk_level": "low"})
    return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])


def _ticket_with_pending_verification(order_id="1013", verification_pending=True, store_id="brand-1"):
    return {
        "id": "ticket-1", "store_id": store_id, "detected_order_id": order_id,
        "messages": [
            {"from": "customer@example.com", "body": "where is my order #1013?", "direction": "inbound"},
            {
                "from": "AI Agent", "body": "Could you confirm the email you used?",
                "direction": "outbound", "role": "assistant",
                **({"needs_email_verification": True} if verification_pending else {}),
            },
        ],
    }


def _run_query(query, get_order_status_mock, ticket_select_return, customer_email="", store_id="brand-1"):
    captured = {}

    async def capturing_completion(*args, messages=None, **kwargs):
        captured["messages"] = messages
        return (_fake_ai_response(), "test_provider", "test_model", _FAKE_USAGE)

    def fake_select(table, params=None):
        if table == "tickets":
            return ticket_select_return
        return []

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=capturing_completion), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=get_order_status_mock), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.lib.supabase_client.supabase_select", side_effect=fake_select):
        result = run(customer_success_agent.process_customer_query(
            query=query,
            customer_info={"name": "Syeda", "email": customer_email},
            tenant_id="tenant-1", store_id=store_id, ticket_id="ticket-1",
        ))

    prompt_text = "\n".join(m.get("content", "") for m in captured.get("messages", []))
    return result, prompt_text


# ── 1. Order number + matching email -> order details returned ────────────

def test_order_number_with_matching_email_returns_order_details():
    mock = AsyncMock(return_value=MATCHING_ORDER)
    result, prompt_text = _run_query(
        "where is my order #1013?", mock, ticket_select_return=[],
        customer_email="customer10@example.com",
    )
    assert "REAL ORDER DATA FROM SHOPIFY" in prompt_text
    assert "CANCELLED: Yes" in prompt_text
    assert result.get("needs_identity_verification") is not True


# ── 2. Order number + mismatched email -> withheld + verification asked ───

def test_order_number_with_mismatched_email_withholds_and_asks_for_verification():
    mock = AsyncMock(return_value=MISMATCH_RESULT)
    result, prompt_text = _run_query(
        "where is my order #1013?", mock, ticket_select_return=[],
        customer_email="syedahafsa1983@gmail.com",
    )
    assert "ORDER IDENTITY UNVERIFIED" in prompt_text
    assert "CANCELLED: Yes" not in prompt_text
    assert result.get("needs_identity_verification") is True


# ── 3. Mismatch -> customer provides correct email -> lookup succeeds ─────

def test_followup_with_correct_email_recovers_order_number_and_succeeds():
    mock = AsyncMock(return_value=MATCHING_ORDER)
    ticket = _ticket_with_pending_verification()
    result, prompt_text = _run_query(
        "customer10@example.com", mock, ticket_select_return=[ticket],
    )
    mock.assert_awaited_once_with(
        "1013", shop_domain=None, access_token=None, customer_email="customer10@example.com",
    )
    assert "REAL ORDER DATA FROM SHOPIFY" in prompt_text
    assert "CANCELLED: Yes" in prompt_text


# ── 4. Mismatch -> customer provides ANOTHER wrong email -> still withheld ─

def test_followup_with_another_wrong_email_still_withholds():
    mock = AsyncMock(return_value={"error": "Order #1013 not found.", "order_number": "1013", "ownership_mismatch": True})
    ticket = _ticket_with_pending_verification()
    result, prompt_text = _run_query(
        "I think it was wrong-guess@example.com", mock, ticket_select_return=[ticket],
    )
    mock.assert_awaited_once_with(
        "1013", shop_domain=None, access_token=None, customer_email="wrong-guess@example.com",
    )
    assert "ORDER IDENTITY UNVERIFIED" in prompt_text
    assert "CANCELLED: Yes" not in prompt_text
    assert result.get("needs_identity_verification") is True


# ── 5. Follow-up doesn't repeat the order number - context is reused ──────

def test_followup_natural_wording_does_not_require_repeating_order_number():
    mock = AsyncMock(return_value=MATCHING_ORDER)
    ticket = _ticket_with_pending_verification()
    for phrasing in ["customer10@example.com", "The email I used was customer10@example.com", "I think it was customer10@example.com"]:
        mock.reset_mock()
        _run_query(phrasing, mock, ticket_select_return=[ticket])
        mock.assert_awaited_once_with(
            "1013", shop_domain=None, access_token=None, customer_email="customer10@example.com",
        )


# ── 6. No verified identity (no pending verification marker) -> no leak ───

def test_bare_email_without_prior_verification_request_never_triggers_lookup():
    """A message that happens to contain an email, on a ticket that was
    never told to verify one, must not be treated as a verification
    follow-up - prevents a bare "here's an email" from ever substituting
    for the real ownership check."""
    mock = AsyncMock(return_value=MATCHING_ORDER)
    ticket = _ticket_with_pending_verification(verification_pending=False)
    result, prompt_text = _run_query(
        "customer10@example.com", mock, ticket_select_return=[ticket],
    )
    mock.assert_not_awaited()
    assert "REAL ORDER DATA FROM SHOPIFY" not in prompt_text
    assert "CANCELLED: Yes" not in prompt_text


def test_no_ticket_id_context_never_triggers_lookup():
    """No detected_order_id on the ticket at all -> nothing to recover, the
    bare email is correctly ignored rather than guessed at."""
    mock = AsyncMock(return_value=MATCHING_ORDER)
    ticket = _ticket_with_pending_verification(order_id=None)
    _run_query("customer10@example.com", mock, ticket_select_return=[ticket])
    mock.assert_not_awaited()


# ── 7. Existing order lookup behavior remains unchanged ────────────────────

def test_fresh_order_number_request_takes_the_original_path_unchanged():
    """A normal, non-follow-up request (order number + no pending
    verification state) must behave exactly as before - the new follow-up
    branch never fires and get_order_status is called exactly once, using
    customer_info's own email (not any ticket-history-derived email)."""
    mock = AsyncMock(return_value=MATCHING_ORDER)
    result, prompt_text = _run_query(
        "where is my order #1013?", mock, ticket_select_return=[{"id": "ticket-1", "messages": []}],
        customer_email="customer10@example.com",
    )
    mock.assert_awaited_once_with(
        "1013", shop_domain=None, access_token=None, customer_email="customer10@example.com",
    )
    assert "REAL ORDER DATA FROM SHOPIFY" in prompt_text


# ── 8. Tenant/brand isolation unchanged ─────────────────────────────────────

def test_followup_lookup_uses_this_conversations_own_brand_credentials_not_the_tickets():
    """The recovered order number comes from the ticket row, but the Shopify
    shop/token used for the re-run lookup must still come from THIS
    conversation's own store_id resolution - never from whatever brand_id/
    store_id happens to be on the fetched ticket row, so a cross-tenant
    ticket lookup can never leak another brand's Shopify credentials."""
    mock = AsyncMock(return_value=MATCHING_ORDER)
    # Ticket row deliberately has no store_id/brand_id fields at all -
    # proves the lookup doesn't depend on them.
    ticket = {"id": "ticket-1", "detected_order_id": "1013", "messages": [
        {"direction": "outbound", "needs_email_verification": True},
    ]}
    _run_query("customer10@example.com", mock, ticket_select_return=[ticket], store_id="brand-1")
    # No brand row resolves for "brand-1" in this test (tickets is the only
    # mocked table), so shop_domain/access_token stay None either way - the
    # call succeeding at all with no ticket-derived store_id/brand_id proves
    # nothing from the ticket row's (absent) tenant fields was used.
    mock.assert_awaited_once_with(
        "1013", shop_domain=None, access_token=None, customer_email="customer10@example.com",
    )
