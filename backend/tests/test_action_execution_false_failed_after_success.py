"""
P0 fix regression tests: a Shopify mutation that succeeded could be
converted into a retryable FAILED action status by a downstream DB/
notification failure, risking a real double-refund/double-cancel on retry.

Root cause (actions_service.py::approve_action and v2_actions.py's
/approve route): the single exception boundary wrapped not just the
Shopify call but also the subsequent status-update write, event log, cache
invalidation, and merchant notification. If any of those threw AFTER
Shopify already executed successfully, the outer `except Exception` marked
the action FAILED - and FAILED is retryable (approve_action accepts
pending/failed; v2_actions.py's /retry resets failed -> pending), so a
merchant retry would call Shopify a second time for work that already
happened.

Fix: everything after a successful Shopify call is now isolated in its own
try/except that logs but never re-raises into the outer handler, so
`_mark_failed`/a "failed" status write can never happen once Shopify has
already executed. final_result is built from execution_result alone and
always returned regardless of bookkeeping outcome.

multi_brand_actions.py's approve_action is NOT touched — traced separately
and confirmed its own exception handler never demotes an already-"executed"
row back to "failed" (the notify/log calls happen after the state write,
and its outer except only returns an error dict, never touches the DB).
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.actions_service import actions_service  # noqa: E402


def _refund_action(**overrides):
    a = {
        "id": "action-1", "tenant_id": "tenant-1", "brand_id": "brand-1",
        "ticket_id": "ticket-1", "status": "pending", "action_type": "refund",
        "order_id": "1001", "customer_email": "jane@example.com",
        "extracted_data": {},
    }
    a.update(overrides)
    return a


# ── 1/2/4. actions_service.py: success + downstream DB failure ─────────────

@pytest.mark.asyncio
async def test_shopify_success_then_db_failure_does_not_produce_retryable_failed_status():
    """Shopify's refund call succeeds, but the write that marks the action
    EXECUTED throws (simulated Supabase outage). The caller must still see
    success=True (Shopify really did execute), and no write may ever set
    status="failed" - that would make it retryable."""
    action_state = {"status": "pending", "_executed_write_attempted": False}

    def fake_select(table, params=None):
        if table == "actions":
            return [dict(_refund_action(status=action_state["status"]))]
        return []

    def fake_update(table, match, data):
        if table != "actions":
            return {}
        if "status" in match:  # atomic claim: status=in.(pending,failed)
            if action_state["status"] in ("pending", "failed"):
                action_state["status"] = "approved"
                return [dict(_refund_action(status="approved"))]
            return []
        if data.get("status") == "executed":
            if not action_state["_executed_write_attempted"]:
                action_state["_executed_write_attempted"] = True
                raise Exception("simulated Supabase outage writing executed status")
            action_state["status"] = "executed"
            return [dict(_refund_action(status="executed"))]
        if data.get("status") == "failed":
            action_state["status"] = "failed"
            return [dict(_refund_action(status="failed"))]
        return [dict(_refund_action(status=action_state["status"]))]

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update) as mock_update, \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_client_getter, \
         patch.object(actions_service, "_post_execution_notify", new=AsyncMock(side_effect=Exception("notify down too"))), \
         patch.object(actions_service, "_log_event", new=AsyncMock()):
        mock_client = MagicMock()
        mock_client.process_refund = AsyncMock(return_value={"success": True, "message": "Refund processed"})
        mock_client_getter.return_value = mock_client

        result = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="merchant@example.com"
        )

    assert result["success"] is True, (
        f"GAP: Shopify executed successfully but the caller was told it failed: {result}"
    )
    mock_client.process_refund.assert_awaited_once()

    failed_writes = [c for c in mock_update.call_args_list if c.args[2].get("status") == "failed"]
    assert failed_writes == [], (
        f"GAP: a Shopify-success action was marked 'failed' after a downstream error: {failed_writes}"
    )
    assert action_state["status"] == "approved", (
        "Action should be left at 'approved' (non-retryable) when only the terminal "
        "status write itself failed - never demoted to the retryable 'failed' state."
    )


@pytest.mark.asyncio
async def test_retry_after_post_execution_failure_cannot_re_execute_shopify():
    """Following directly from the scenario above: a second approve_action
    call for the same action_id (simulating a merchant retry) must NOT call
    Shopify again, because the action is no longer sitting in a
    pending/failed (retryable) status."""
    action_state = {"status": "pending", "_executed_write_attempted": False}

    def fake_select(table, params=None):
        if table == "actions":
            return [dict(_refund_action(status=action_state["status"]))]
        return []

    def fake_update(table, match, data):
        if table != "actions":
            return {}
        if "status" in match:
            if action_state["status"] in ("pending", "failed"):
                action_state["status"] = "approved"
                return [dict(_refund_action(status="approved"))]
            return []
        if data.get("status") == "executed":
            if not action_state["_executed_write_attempted"]:
                action_state["_executed_write_attempted"] = True
                raise Exception("simulated Supabase outage writing executed status")
            action_state["status"] = "executed"
            return [dict(_refund_action(status="executed"))]
        if data.get("status") == "failed":
            action_state["status"] = "failed"
            return [dict(_refund_action(status="failed"))]
        return [dict(_refund_action(status=action_state["status"]))]

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_client_getter, \
         patch.object(actions_service, "_post_execution_notify", new=AsyncMock()), \
         patch.object(actions_service, "_log_event", new=AsyncMock()):
        mock_client = MagicMock()
        mock_client.process_refund = AsyncMock(return_value={"success": True, "message": "Refund processed"})
        mock_client_getter.return_value = mock_client

        first = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="merchant@example.com"
        )
        second = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="merchant@example.com"
        )

    assert first["success"] is True
    assert second["success"] is False
    assert "already" in second["error"].lower()
    mock_client.process_refund.assert_awaited_once(), (
        "GAP: Shopify's refund mutation was called a second time on retry after a "
        "downstream bookkeeping failure - real double-refund risk."
    )


# ── 3/5. Existing refund/cancel approval behavior is unaffected ────────────

@pytest.mark.asyncio
async def test_normal_refund_approval_without_any_downstream_failure_still_succeeds():
    action = _refund_action()
    update_calls = []

    def fake_update(table, match, data):
        update_calls.append((match, data))
        return [action]

    with patch("src.services.actions_service.supabase_select", return_value=[action]), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_client_getter, \
         patch.object(actions_service, "_post_execution_notify", new=AsyncMock()), \
         patch.object(actions_service, "_log_event", new=AsyncMock()):
        mock_client = MagicMock()
        mock_client.process_refund = AsyncMock(return_value={"success": True, "message": "Refund processed"})
        mock_client_getter.return_value = mock_client

        result = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="merchant@example.com"
        )

    assert result["success"] is True
    mock_client.process_refund.assert_awaited_once()
    # The normal path must still actually reach the correct terminal status -
    # not just report success in-memory while the bookkeeping isolation
    # silently swallows a real write.
    executed_writes = [d for _, d in update_calls if d.get("status") == "executed"]
    assert len(executed_writes) == 1, (
        f"Normal successful approval did not persist the EXECUTED terminal status: {update_calls}"
    )
