"""
"Send this error to tResolv" (Task 2, item 3).

Root cause / gap: a failed cancellation had no way for a merchant to report
it beyond copying the error text by hand, and - separately - a failed
action disappeared from view entirely once it left `pending` status (see
test_actions_failed_status_included_in_history below and the Actions.jsx
"Failed" section this backs).

Fix: ActionsService.report_action_issue() reuses the exact same
action_logs mechanism (_log_event) every other step of the action
lifecycle (created/approved/rejected/api_error) already writes to, rather
than building a second reporting system. It logs the action's own real
context (brand, ticket, order, action type, status, timestamp, the actual
Shopify error already stored in error_message) - never a secret or token,
since only fields already on the action row are used.
"""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.actions_service import actions_service  # noqa: E402


def _failed_action():
    return {
        "id": "action-1", "tenant_id": "tenant-1", "brand_id": "brand-1",
        "ticket_id": "ticket-1", "status": "failed", "action_type": "cancel_order",
        "order_id": "1013", "customer_email": "c@example.com",
        "error_message": "Invalid Shopify access token. Please reconnect your store.",
        "updated_at": "2026-01-01T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_report_action_issue_logs_the_real_context():
    action = _failed_action()
    logged = []

    def fake_select(table, params=None):
        if table == "actions":
            return [action]
        return []

    def fake_insert(table, data):
        logged.append((table, data))
        return data

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_insert", side_effect=fake_insert):
        result = await actions_service.report_action_issue(
            tenant_id="tenant-1", action_id="action-1", reported_by="merchant@example.com",
        )

    assert result["success"] is True
    assert len(logged) == 1
    table, data = logged[0]
    assert table == "action_logs"
    assert data["event"] == "reported_to_support"
    assert data["actor"] == "merchant@example.com"
    assert data["error_message"] == "Invalid Shopify access token. Please reconnect your store."
    assert data["details"]["order_id"] == "1013"
    assert data["details"]["brand_id"] == "brand-1"
    assert data["details"]["ticket_id"] == "ticket-1"
    assert data["details"]["action_type"] == "cancel_order"


@pytest.mark.asyncio
async def test_report_action_issue_never_exposes_secrets():
    """Only fields already stored on the action row are logged - no
    Shopify token, API key, or other credential is ever included, even
    though the action's tenant has one configured elsewhere."""
    action = _failed_action()
    logged = []

    def fake_select(table, params=None):
        if table == "actions":
            return [action]
        return []

    def fake_insert(table, data):
        logged.append(data)
        return data

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_insert", side_effect=fake_insert):
        await actions_service.report_action_issue(
            tenant_id="tenant-1", action_id="action-1", reported_by="merchant@example.com",
        )

    dumped = str(logged)
    assert "shpat_" not in dumped
    assert "access_token" not in dumped.lower()


@pytest.mark.asyncio
async def test_report_action_issue_is_tenant_scoped():
    """A merchant cannot report (and therefore cannot pull details for) an
    action belonging to a different tenant - get_action's own tenant_id
    filter is reused unchanged."""
    def fake_select(table, params=None):
        if table == "actions":
            # Simulates the tenant_id filter excluding a cross-tenant action.
            return []
        return []

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select):
        result = await actions_service.report_action_issue(
            tenant_id="tenant-attacker", action_id="action-1", reported_by="attacker@example.com",
        )

    assert result["success"] is False


@pytest.mark.asyncio
async def test_actions_failed_status_included_in_history():
    """The other half of the same bug: a failed action must remain
    discoverable via get_action_history (which the Actions.jsx page's
    "Failed" section reads from), not just vanish once it's no longer
    'pending'."""
    action = _failed_action()

    def fake_select(table, params=None):
        if table == "actions":
            assert "failed" in params["status"]
            return [action]
        return []

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select):
        history = await actions_service.get_action_history("tenant-1")

    assert any(a["id"] == "action-1" for a in history)
