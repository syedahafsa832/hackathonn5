"""
saas_actions.py / actions_service.py Hardening Tests
========================================================
This is the second, separate live path that executes refund/cancel_order
against Shopify (distinct from v2_tickets.py's execute_refund/
execute_cancel_order, which already had these protections). Covers:
idempotency-key replay, per-org rate limiting on the route, and that every
attempt (success or failure) is recorded in financial_action_audit_log.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.actions_service import actions_service, ActionStatus  # noqa: E402
import src.services.financial_audit as financial_audit  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_rate_buckets():
    financial_audit._rate_buckets.clear()
    yield
    financial_audit._rate_buckets.clear()


def _action(status="pending", action_type="cancel_order", ticket_id="ticket-1"):
    return {
        "id": "action-1", "tenant_id": "tenant-1", "brand_id": "brand-1",
        "ticket_id": ticket_id, "status": status, "action_type": action_type,
        "order_id": "1001", "customer_email": "c@example.com",
        "extracted_data": {},
    }


@pytest.mark.asyncio
async def test_idempotency_replay_skips_shopify_call_entirely():
    action = _action()
    audit_rows = []

    def fake_select(table, params=None):
        if table == "actions":
            return [action]
        return []

    def fake_audit_insert(table, data):
        audit_rows.append(data)
        return data

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", return_value=[action]), \
         patch("src.services.financial_audit.supabase_select", side_effect=lambda t, p=None: audit_rows), \
         patch("src.services.financial_audit.supabase_insert", side_effect=fake_audit_insert), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_client_getter:
        mock_client = MagicMock()
        mock_client.cancel_order = AsyncMock(return_value={"success": True, "order_name": "#1001"})
        mock_client_getter.return_value = mock_client

        first = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="staff@example.com",
            idempotency_key="retry-key",
        )
        assert first["success"] is True
        assert mock_client.cancel_order.call_count == 1

        # Second call: same idempotency key, action row still says "approved"
        # (already claimed) — without the idempotency check this would return
        # "Action already approved" instead of replaying the real result.
        second = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="staff@example.com",
            idempotency_key="retry-key",
        )
        assert second == first
        assert mock_client.cancel_order.call_count == 1  # not called again


@pytest.mark.asyncio
async def test_failed_attempt_is_recorded_in_audit_log():
    action = _action()
    audit_rows = []

    def fake_select(table, params=None):
        if table == "actions":
            return [action]
        return []

    def fake_audit_insert(table, data):
        audit_rows.append(data)
        return data

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", return_value=[action]), \
         patch("src.services.financial_audit.supabase_select", return_value=[]), \
         patch("src.services.financial_audit.supabase_insert", side_effect=fake_audit_insert), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_client_getter:
        mock_client = MagicMock()
        from src.services.shopify_service import ShopifyError
        mock_client.cancel_order = AsyncMock(side_effect=ShopifyError("Order already cancelled.", "order_already_cancelled"))
        mock_client_getter.return_value = mock_client

        result = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="staff@example.com",
        )

    assert result["success"] is False
    assert len(audit_rows) == 1
    assert audit_rows[0]["status"] == "failed"
    assert audit_rows[0]["action_type"] == "cancel_order"


@pytest.mark.asyncio
async def test_non_financial_action_types_are_not_audited():
    """change_address/reship/restore_order don't move money — they're
    intentionally outside the audit table's CHECK constraint."""
    action = _action(action_type="reship")
    audit_calls = []

    def fake_select(table, params=None):
        if table == "actions":
            return [action]
        return []

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", return_value=[action]), \
         patch("src.services.financial_audit.supabase_insert", side_effect=lambda t, d: audit_calls.append(d)), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock(return_value=MagicMock())):
        result = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="staff@example.com",
        )

    assert result["success"] is True
    assert audit_calls == []


@pytest.mark.asyncio
async def test_failed_action_can_be_retried_and_succeed():
    """Persistent Shopify failure UX: a merchant must be able to retry a
    failed action (e.g. Shopify was briefly down) from the same approve
    flow, not just watch it disappear. A 'failed' action is now claimable
    exactly like a 'pending' one."""
    action = _action(status="failed")

    def fake_select(table, params=None):
        if table == "actions":
            return [action]
        return []

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", return_value=[action]) as mock_update, \
         patch("src.services.financial_audit.supabase_select", return_value=[]), \
         patch("src.services.financial_audit.supabase_insert"), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_client_getter:
        mock_client = MagicMock()
        mock_client.cancel_order = AsyncMock(return_value={"success": True, "order_name": "#1001"})
        mock_client_getter.return_value = mock_client

        result = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="staff@example.com",
        )

    assert result["success"] is True
    mock_client.cancel_order.assert_awaited_once()
    # The atomic claim must have matched the action while it was still
    # 'failed' — not silently skipped straight to executing without ever
    # re-claiming it.
    claim_call = mock_update.call_args_list[0]
    assert claim_call.args[1]["status"] == "in.(pending,failed)"


@pytest.mark.asyncio
async def test_already_approved_action_still_cannot_be_reapproved():
    """Retry only ever applies to 'failed' — every other terminal/in-flight
    status (approved, executed, rejected) is unchanged and still rejected."""
    for status in ("approved", "executed", "rejected"):
        action = _action(status=status)

        def fake_select(table, params=None, _action=action):
            if table == "actions":
                return [_action]
            return []

        with patch("src.services.actions_service.supabase_select", side_effect=fake_select):
            result = await actions_service.approve_action(
                tenant_id="tenant-1", action_id="action-1", approved_by="staff@example.com",
            )
        assert result["success"] is False
        assert status in result["error"]


@pytest.mark.asyncio
async def test_unexpected_exception_never_leaks_internal_detail_to_merchant():
    """A genuinely unexpected exception (not a curated ShopifyError) must
    never reach the merchant-facing error/error_message fields verbatim —
    only a generic, safe message. The real exception text is still logged
    and kept in the audit trail (staff-only), just never in what the
    dashboard's Failed Actions section renders."""
    action = _action()
    audit_rows = []

    def fake_select(table, params=None):
        if table == "actions":
            return [action]
        return []

    def fake_audit_insert(table, data):
        audit_rows.append(data)
        return data

    captured_mark_failed = {}

    async def fake_mark_failed(action_id, message, error_code=None):
        captured_mark_failed["message"] = message

    sensitive_detail = "https://internal-db.example.com/secret-path?token=abc123 ConnectionError"

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", return_value=[action]), \
         patch("src.services.financial_audit.supabase_select", return_value=[]), \
         patch("src.services.financial_audit.supabase_insert", side_effect=fake_audit_insert), \
         patch.object(actions_service, "_mark_failed", new=AsyncMock(side_effect=fake_mark_failed)), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant",
               new=AsyncMock(side_effect=Exception(sensitive_detail))):
        result = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="staff@example.com",
        )

    assert result["success"] is False
    assert sensitive_detail not in result["error"]
    assert "token=" not in result["error"]
    assert sensitive_detail not in captured_mark_failed["message"]
    # The real detail is still preserved for staff, just not merchant-facing.
    assert audit_rows[0]["error_detail"] == sensitive_detail


def test_route_rate_limits_at_eleventh_request_per_tenant():
    org = "rate-limit-test-tenant"
    for _ in range(10):
        assert financial_audit.check_org_rate_limit(org) is True
    assert financial_audit.check_org_rate_limit(org) is False
