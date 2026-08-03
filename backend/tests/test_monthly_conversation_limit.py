"""
Starter's "500 conversations per month" cap (landing page pricing table).

Unlike every other daily-resetting resource, Starter's ticket/conversation
limit resets monthly, not daily — plan_service.check_limit()/record_usage()
route "tickets" through a separate usage_month/usage_tickets_this_month
counter (migration 034) whenever the plan defines tickets_per_month instead
of tickets_per_day. Growth and Scale are unlimited (tickets_per_month=None),
so they're never gated at all.
"""
import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import src.services.plan_service as ps

THIS_MONTH = datetime.now(timezone.utc).strftime("%Y-%m")


def _mocked(tenant):
    def fake_select(table, params=None):
        return [tenant] if table == "tenants" else []

    def fake_update(table, match, data):
        tenant.update(data)
        return [tenant]

    return patch("src.services.plan_service.supabase_select", side_effect=fake_select), \
           patch("src.services.plan_service.supabase_update", side_effect=fake_update)


def test_starter_allows_usage_under_the_monthly_cap():
    tenant = {"id": "t1", "email": "user@example.com", "plan": "starter",
              "usage_month": THIS_MONTH, "usage_tickets_this_month": 499}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.check_limit("t1", "tickets")
    assert result["allowed"] is True
    assert result["limit"] == 500
    assert result["used"] == 499
    assert result["remaining"] == 1


def test_starter_blocks_at_the_monthly_cap():
    tenant = {"id": "t2", "email": "user@example.com", "plan": "starter",
              "usage_month": THIS_MONTH, "usage_tickets_this_month": 500}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.check_limit("t2", "tickets")
    assert result["allowed"] is False
    assert result["reason"] == "monthly_limit_reached"
    assert result["upgrade_required"] is True


def test_starter_monthly_counter_resets_on_a_new_month_not_a_new_day():
    # A stale usage_month (not this month) should reset to 0, even though
    # this tenant already "used" 500 under last month's counter.
    tenant = {"id": "t3", "email": "user@example.com", "plan": "starter",
              "usage_month": "2020-01", "usage_tickets_this_month": 500}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.check_limit("t3", "tickets")
    assert result["allowed"] is True
    assert result["used"] == 0
    assert tenant["usage_month"] == THIS_MONTH
    assert tenant["usage_tickets_this_month"] == 0


def test_starter_record_usage_increments_the_monthly_counter_not_the_daily_one():
    today = datetime.now(timezone.utc).date().isoformat()
    tenant = {"id": "t4", "email": "user@example.com", "plan": "starter",
              "usage_month": THIS_MONTH, "usage_tickets_this_month": 10,
              "usage_date": today, "usage_tickets_today": 0}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        ps.record_usage("t4", "tickets")
    assert tenant["usage_tickets_this_month"] == 11
    assert tenant["usage_tickets_today"] == 0  # untouched — starter doesn't use the daily counter


def test_growth_has_no_conversation_cap():
    tenant = {"id": "t5", "email": "user@example.com", "plan": "growth",
              "usage_month": THIS_MONTH, "usage_tickets_this_month": 999999}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.check_limit("t5", "tickets")
    assert result["allowed"] is True
    assert result["limit"] is None


def test_free_plan_is_unaffected_and_still_uses_the_daily_counter():
    today = datetime.now(timezone.utc).date().isoformat()
    tenant = {"id": "t6", "email": "user@example.com", "plan": "free",
              "usage_date": today, "usage_tickets_today": 9}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.check_limit("t6", "tickets")
    assert result["allowed"] is True
    assert result["limit"] == 10
    assert result["used"] == 9


def test_founding_slots_remaining_counts_claimed_tenants():
    claimed = [{"id": f"t{i}"} for i in range(5)]
    with patch("src.services.plan_service.supabase_select", return_value=claimed):
        remaining = ps.get_founding_slots_remaining()
    assert remaining == 15


def test_founding_slots_remaining_floors_at_zero():
    claimed = [{"id": f"t{i}"} for i in range(25)]
    with patch("src.services.plan_service.supabase_select", return_value=claimed):
        remaining = ps.get_founding_slots_remaining()
    assert remaining == 0
