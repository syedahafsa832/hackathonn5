"""
Scope-aware 403 handling on the Shopify action-execution path.

Before this fix, ShopifyClient._handle_response() had no branch for HTTP 403
(Shopify's "token lacks a required scope" response) — it fell through to the
generic UNKNOWN_ERROR branch, which echoes Shopify's raw error text back
verbatim (e.g. "This action requires merchant approval for write_orders
scope"). Fine for a merchant, not something to relay to a customer, and not
classified distinctly enough for a dashboard/health page to say "reconnect"
specifically rather than a generic failure.

These tests cover, per the task brief:
  - a 403 is classified as MISSING_SCOPE with a clean, non-raw message
  - it is never retried (only RATE_LIMITED retries in _request())
  - approve_action() ends the action at status=failed, never
    approved/executed - the existing safe action state is preserved
  - the customer is never notified of a false success (_post_execution_notify
    is only ever called on a genuine success, so a scope failure produces no
    customer-facing claim at all - silence, not a lie)
  - the raw Shopify error body never appears in what's stored/returned
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("ENCRYPTION_SECRET", "test-encryption-secret-do-not-use-in-prod")

import asyncio  # noqa: E402
from src.services.actions_service import actions_service  # noqa: E402
from src.services.shopify_service import ShopifyClient, ShopifyError, ShopifyErrorCode  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# 1. ShopifyClient._handle_response classifies 403 distinctly
# ---------------------------------------------------------------------------

def _fake_403_response(raw_shopify_text='{"errors":"This action requires merchant approval for write_orders scope"}'):
    resp = MagicMock()
    resp.status_code = 403
    resp.headers = {}
    resp.json.return_value = {"errors": "This action requires merchant approval for write_orders scope"}
    resp.text = raw_shopify_text
    return resp


def test_403_is_classified_as_missing_scope_not_unknown_error():
    client = ShopifyClient("test-shop.myshopify.com", "fake-token")
    resp = _fake_403_response()

    with pytest.raises(ShopifyError) as exc_info:
        client._handle_response(resp, context="orders/1/cancel.json")

    err = exc_info.value
    assert err.error_code == ShopifyErrorCode.MISSING_SCOPE
    assert err.status_code == 403


def test_403_error_message_never_contains_the_raw_shopify_error_text():
    """The merchant/customer-facing message must be tResolv's own wording,
    not Shopify's raw response echoed back (which could contain internal
    scope-handle jargon or other API-specific detail)."""
    client = ShopifyClient("test-shop.myshopify.com", "fake-token")
    resp = _fake_403_response()

    with pytest.raises(ShopifyError) as exc_info:
        client._handle_response(resp, context="orders/1/cancel.json")

    assert "write_orders scope" not in exc_info.value.message
    assert "reconnect" in exc_info.value.message.lower()


def test_403_is_never_retried():
    """Only RATE_LIMITED retries in _request(); a 403 must fail on the
    first attempt, not loop indefinitely trying the same forbidden call."""
    client = ShopifyClient("test-shop.myshopify.com", "fake-token")
    resp = _fake_403_response()

    with patch("src.services.shopify_service.requests.post", return_value=resp) as mock_post:
        with pytest.raises(ShopifyError) as exc_info:
            client._request("POST", "orders/1/cancel.json", data={})

    assert exc_info.value.error_code == ShopifyErrorCode.MISSING_SCOPE
    mock_post.assert_called_once()


# ---------------------------------------------------------------------------
# 2. approve_action() preserves safe state on a missing-scope failure
# ---------------------------------------------------------------------------

def _action(status="pending", action_type="cancel_order"):
    return {
        "id": "action-1", "tenant_id": "tenant-1", "brand_id": "brand-1",
        "ticket_id": "ticket-1", "status": status, "action_type": action_type,
        "order_id": "1001", "customer_email": "customer@example.com", "extracted_data": {},
    }


def _fake_backend(action):
    """Same atomic-claim-aware in-memory double used in
    test_action_lifecycle_safety.py, so this test actually exercises the
    real WHERE-status-guarded update path, not just a mock call count."""
    state = {"row": dict(action)}

    def fake_select(table, params=None):
        if table != "actions":
            return []
        row = state["row"]
        if params and params.get("id") not in (None, f"eq.{row['id']}"):
            return []
        return [dict(row)]

    def fake_update(table, match, data):
        if table != "actions":
            return [data]
        row = state["row"]
        if match.get("id") != f"eq.{row['id']}":
            return []
        if "status" in match and match["status"] != f"eq.{row['status']}":
            return []
        row.update(data)
        return [dict(row)]

    return state, fake_select, fake_update


@pytest.fixture(autouse=True)
def _mock_log_event():
    with patch.object(actions_service, "_log_event", new=AsyncMock()):
        yield


def test_missing_scope_failure_ends_at_failed_never_approved_or_executed():
    action = _action(status="pending")
    state, fake_select, fake_update = _fake_backend(action)

    mock_client = MagicMock()
    mock_client.cancel_order = AsyncMock(
        side_effect=ShopifyError(
            "This Shopify connection is missing a permission this action needs. "
            "Reconnect Shopify to grant the required access.",
            ShopifyErrorCode.MISSING_SCOPE, 403,
        )
    )

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update), \
         patch("src.services.financial_audit.supabase_select", return_value=[]), \
         patch("src.services.financial_audit.supabase_insert", return_value={}), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock(return_value=mock_client)), \
         patch.object(actions_service, "_post_execution_notify", new=AsyncMock()) as mock_notify:
        result = run(actions_service.approve_action("tenant-1", "action-1", "staff@example.com"))

    assert result["success"] is False
    assert result["error_code"] == ShopifyErrorCode.MISSING_SCOPE
    # Never silently stuck "approved" (claimed but never resolved), and
    # never falsely marked executed - a real terminal, safe state.
    assert state["row"]["status"] == "failed"
    assert state["row"]["status"] != "executed"
    assert state["row"]["status"] != "approved"
    # No approval-bypass: exactly one Shopify call was attempted, no retry.
    mock_client.cancel_order.assert_called_once()
    # The customer is never told anything false - silence, not a lie: the
    # only place a customer-facing email is ever sent is _post_execution_notify,
    # and it must never fire on this failure path.
    mock_notify.assert_not_called()


def test_missing_scope_error_message_is_not_the_raw_shopify_body():
    action = _action(status="pending")
    state, fake_select, fake_update = _fake_backend(action)

    mock_client = MagicMock()
    mock_client.cancel_order = AsyncMock(
        side_effect=ShopifyError(
            "This Shopify connection is missing a permission this action needs. "
            "Reconnect Shopify to grant the required access.",
            ShopifyErrorCode.MISSING_SCOPE, 403,
        )
    )

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update), \
         patch("src.services.financial_audit.supabase_select", return_value=[]), \
         patch("src.services.financial_audit.supabase_insert", return_value={}), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock(return_value=mock_client)), \
         patch.object(actions_service, "_post_execution_notify", new=AsyncMock()):
        result = run(actions_service.approve_action("tenant-1", "action-1", "staff@example.com"))

    assert "reconnect" in result["error"].lower()
    assert state["row"]["error_message"] == result["error"]
    assert "errors" not in state["row"]["error_message"]  # not Shopify's raw JSON shape
