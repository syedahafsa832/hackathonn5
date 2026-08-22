"""
Human-escalation / action-lifecycle production-readiness pass.

Two real bugs found and fixed:

1. reject_action() (both actions_service.py and its mirrored v2_actions.py
   route) read the action's status, then wrote an UNCONDITIONAL update with
   no WHERE-status guard - unlike approve_action()'s atomic
   `status=eq.pending` claim. A reject request racing a concurrent
   approve/execute could pass the initial check and then silently overwrite
   an already-approved/executed action's status back to "rejected",
   corrupting the audit trail. Fixed by giving reject_action() the same
   atomic conditional-update claim approve_action() already had.

2. message_processor.py's _check_thread_override() (the Gmail-channel
   human-takeover check) explicitly failed OPEN on a lookup error
   ("return False - don't block on error"), i.e. if we can't confirm
   whether a human has taken over, it allowed the AI to reply anyway -
   the opposite of the chat widget's equivalent check, which already fails
   closed. Fixed to fail closed, matching chat.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.actions_service import actions_service  # noqa: E402
import src.services.financial_audit as financial_audit  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.fixture(autouse=True)
def _reset_rate_buckets():
    financial_audit._rate_buckets.clear()
    yield
    financial_audit._rate_buckets.clear()


@pytest.fixture(autouse=True)
def _mock_log_event():
    # _log_event's real supabase_insert("action_logs", ...) retries against
    # an unreachable localhost in this test env - pure side-effect logging,
    # safe to mock away everywhere in this file for speed.
    with patch.object(actions_service, "_log_event", new=AsyncMock()):
        yield


def _action(status="pending", action_type="cancel_order", tenant_id="tenant-1"):
    return {
        "id": "action-1", "tenant_id": tenant_id, "brand_id": "brand-1",
        "ticket_id": "ticket-1", "status": status, "action_type": action_type,
        "order_id": "1001", "customer_email": "c@example.com", "extracted_data": {},
    }


def _fake_backend(action):
    """A minimal in-memory "actions" table that honors conditional
    (WHERE-status) updates the same way Postgres/Supabase would - needed to
    actually exercise the atomic-claim race-condition fix, not just assert
    a mock was called."""
    state = {"row": dict(action)}

    def fake_select(table, params=None):
        if table != "actions":
            return []
        row = state["row"]
        if params and params.get("id") not in (None, f"eq.{row['id']}"):
            return []
        if params and "tenant_id" in params and params["tenant_id"] != f"eq.{row['tenant_id']}":
            return []
        return [dict(row)]

    def fake_update(table, match, data):
        if table != "actions":
            return [data]
        row = state["row"]
        if match.get("id") != f"eq.{row['id']}":
            return []
        if "status" in match and match["status"] != f"eq.{row['status']}":
            return []  # conditional claim failed - no rows matched
        row.update(data)
        return [dict(row)]

    return state, fake_select, fake_update


# ══════════════════════════════════════════════════════════════════════════
# 5/6. Duplicate/rejected action cannot execute - reject_action race fix
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_rejected_action_cannot_then_be_approved():
    action = _action(status="pending")
    state, fake_select, fake_update = _fake_backend(action)

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update):
        reject_result = await actions_service.reject_action("tenant-1", "action-1", "not eligible")
    assert reject_result["success"] is True
    assert state["row"]["status"] == "rejected"

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update), \
         patch("src.services.financial_audit.supabase_select", return_value=[]), \
         patch("src.services.financial_audit.supabase_insert", return_value={}), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_getter:
        mock_client = MagicMock()
        mock_client.cancel_order = AsyncMock(return_value={"success": True})
        mock_getter.return_value = mock_client
        approve_result = await actions_service.approve_action("tenant-1", "action-1", "staff@example.com")

    assert approve_result["success"] is False
    mock_client.cancel_order.assert_not_called()
    assert state["row"]["status"] == "rejected"  # unchanged - approve never touched it


@pytest.mark.asyncio
async def test_second_reject_request_after_approve_cannot_overwrite_approved_status():
    """The exact race the bug allowed: two requests both pass the initial
    "is it pending?" read, but only one may actually win the write."""
    action = _action(status="pending")
    state, fake_select, fake_update = _fake_backend(action)

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update), \
         patch("src.services.financial_audit.supabase_select", return_value=[]), \
         patch("src.services.financial_audit.supabase_insert", return_value={}), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_getter, \
         patch.object(actions_service, "_log_event", new=AsyncMock()), \
         patch.object(actions_service, "_post_execution_notify", new=AsyncMock()):
        mock_client = MagicMock()
        mock_client.cancel_order = AsyncMock(return_value={"success": True, "order_name": "#1001"})
        mock_getter.return_value = mock_client
        approve_result = await actions_service.approve_action("tenant-1", "action-1", "staff@example.com")
    assert approve_result["success"] is True
    assert state["row"]["status"] == "executed"

    # A reject request that read "pending" a moment earlier now tries to
    # write - must be rejected by the atomic claim, never silently succeed.
    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update):
        reject_result = await actions_service.reject_action("tenant-1", "action-1", "too late")

    assert reject_result["success"] is False
    assert state["row"]["status"] == "executed"  # still executed, not corrupted to "rejected"


@pytest.mark.asyncio
async def test_double_reject_only_the_first_succeeds():
    action = _action(status="pending")
    state, fake_select, fake_update = _fake_backend(action)

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update):
        first = await actions_service.reject_action("tenant-1", "action-1", "first reason")
        second = await actions_service.reject_action("tenant-1", "action-1", "second reason")

    assert first["success"] is True
    assert second["success"] is False
    assert state["row"]["rejection_reason"] == "first reason"


# ══════════════════════════════════════════════════════════════════════════
# 7. Wrong-tenant action cannot be accessed/rejected/approved
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_wrong_tenant_cannot_approve_or_reject_another_tenants_action():
    action = _action(status="pending", tenant_id="tenant-owner")
    state, fake_select, fake_update = _fake_backend(action)

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update):
        reject_result = await actions_service.reject_action("tenant-attacker", "action-1", "not mine")
    assert reject_result["success"] is False
    assert reject_result["error"] == "Action not found"
    assert state["row"]["status"] == "pending"

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_getter:
        mock_client = MagicMock()
        mock_client.cancel_order = AsyncMock(return_value={"success": True})
        mock_getter.return_value = mock_client
        approve_result = await actions_service.approve_action("tenant-attacker", "action-1", "attacker@example.com")

    assert approve_result["success"] is False
    mock_client.cancel_order.assert_not_called()
    assert state["row"]["status"] == "pending"  # untouched


# ══════════════════════════════════════════════════════════════════════════
# 1/2/3. Human takeover - Gmail channel fails CLOSED, matching chat
# ══════════════════════════════════════════════════════════════════════════

def _processor():
    from src.workers.message_processor import UnifiedMessageProcessor
    return UnifiedMessageProcessor()


def test_gmail_takeover_check_fails_closed_on_lookup_error():
    proc = _processor()
    with patch("src.workers.message_processor.supabase_select", side_effect=RuntimeError("db unreachable")):
        result = run(proc._check_thread_override("customer@example.com", "Order question"))
    # Previously returned False here (fail open) - a DB blip must never
    # silently let the AI reply on a thread we can't confirm is AI-owned.
    assert result is True


def test_gmail_takeover_check_returns_false_when_genuinely_no_override_exists():
    """Regression guard: a normal, successful "no active overrides" query
    must NOT be affected by the fail-closed fix - only actual lookup
    failures should fail closed."""
    proc = _processor()
    with patch("src.workers.message_processor.supabase_select", return_value=[]):
        result = run(proc._check_thread_override("customer@example.com", "Order question"))
    assert result is False


def test_gmail_takeover_check_returns_true_for_a_real_active_override():
    proc = _processor()
    active_override = {"conversation_id": "ticket-1", "active": True}
    matching_ticket = {"id": "ticket-1", "customer_email": "customer@example.com", "subject": "Order question"}
    with patch("src.workers.message_processor.supabase_select", return_value=[active_override]), \
         patch("src.workers.message_processor.supabase_service.get_ticket_by_id", new=AsyncMock(return_value=matching_ticket)):
        result = run(proc._check_thread_override("customer@example.com", "Order question"))
    assert result is True
