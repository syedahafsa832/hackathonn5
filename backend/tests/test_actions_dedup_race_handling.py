"""
Defense-in-depth for the "duplicate approvals" bug: every dedup check
before this (return_actions_integration.py's _find_active_action,
actions_service.detect_and_create) is a check-then-insert done in
application code — a genuine race (two concurrent requests, a retried
webhook, overlapping email polls) can pass the "no existing action" check
on both sides before either commits its insert.

migration 053 adds a partial unique index (tenant_id, order_id,
action_type) WHERE status IN ('pending','approved','executed') as a DB-level
backstop. supabase_insert() already logs a 409 Conflict as a "handled race
condition" (see src/lib/supabase_client.py) — these tests cover
actions_service.create_action()'s new handling of that 409: instead of
surfacing a raw failure, it looks up and returns the row that actually won
the race, in the same duplicate_skipped shape detect_and_create() already
uses for its own (non-concurrent) dedup hits.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.actions_service import actions_service  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def test_409_conflict_returns_the_existing_action_not_a_raw_failure():
    winning_row = {"id": "winner-action-id", "action_type": "cancel_order", "status": "pending"}

    def fake_select(table, params=None):
        if table == "actions":
            return [winning_row]
        return []

    with patch("src.services.actions_service.supabase_insert", side_effect=Exception("409 Client Error: Conflict")), \
         patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch.object(actions_service, "_calculate_risk", return_value=("low", [])), \
         patch.object(actions_service, "_log_event"):
        result = run(actions_service.create_action(
            tenant_id="tenant-1", action_type="cancel_order",
            customer_email="c@example.com", order_id="1002",
        ))

    assert result["success"] is True
    assert result["status"] == "duplicate_skipped"
    assert result["action_id"] == "winner-action-id"


def test_non_409_errors_still_surface_as_a_real_failure():
    """Only a genuine 409 gets the duplicate-lookup treatment - any other
    DB error must still surface honestly, not be silently swallowed as if
    it were a duplicate."""
    with patch("src.services.actions_service.supabase_insert", side_effect=Exception("500 Internal Server Error")), \
         patch.object(actions_service, "_calculate_risk", return_value=("low", [])):
        result = run(actions_service.create_action(
            tenant_id="tenant-1", action_type="cancel_order",
            customer_email="c@example.com", order_id="1002",
        ))

    assert result["success"] is False
    assert "500" in result["error"]


def test_409_with_lookup_failure_still_reports_a_failure_not_a_crash():
    """If the post-409 lookup itself fails, create_action must degrade to a
    plain failure result rather than raising - never crash the caller over
    a best-effort duplicate lookup."""
    with patch("src.services.actions_service.supabase_insert", side_effect=Exception("409 Conflict")), \
         patch("src.services.actions_service.supabase_select", side_effect=Exception("lookup boom")), \
         patch.object(actions_service, "_calculate_risk", return_value=("low", [])):
        result = run(actions_service.create_action(
            tenant_id="tenant-1", action_type="cancel_order",
            customer_email="c@example.com", order_id="1002",
        ))

    assert result["success"] is False
