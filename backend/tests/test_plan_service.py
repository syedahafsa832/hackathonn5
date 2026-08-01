"""
Plan / Trial / Super-Admin Tests
=================================
Covers: super-admin bypass, free-plan daily limit, active vs expired trial
(including the auto-downgrade-to-free write), and lazy daily usage reset.
"""
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import src.services.plan_service as ps

TODAY = datetime.now(timezone.utc).date().isoformat()


def _mocked(tenant):
    """Context manager patching supabase_select/update against a single fake tenant row."""
    def fake_select(table, params=None):
        return [tenant] if table == "tenants" else []

    def fake_update(table, match, data):
        tenant.update(data)
        return [tenant]

    return patch("src.services.plan_service.supabase_select", side_effect=fake_select), \
           patch("src.services.plan_service.supabase_update", side_effect=fake_update)


def test_super_admin_email_matching_is_case_insensitive():
    assert ps.is_super_admin("syedahafsa772@gmail.com") is True
    assert ps.is_super_admin("SyedaHafsa772@Gmail.com") is True
    assert ps.is_super_admin("someone.else@example.com") is False
    assert ps.is_super_admin(None) is False


def test_super_admin_bypasses_daily_limit_regardless_of_usage():
    tenant = {"id": "t1", "email": "syedahafsa772@gmail.com", "plan": "free",
              "usage_date": TODAY, "usage_tickets_today": 999999}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.can_process_ticket("t1")
    assert result["allowed"] is True
    assert result["plan"] == "super_admin"


def test_free_plan_blocks_at_limit_with_structured_reason():
    tenant = {"id": "t2", "email": "user@example.com", "plan": "free",
              "usage_date": TODAY, "usage_tickets_today": 10}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.can_process_ticket("t2")
    assert result["allowed"] is False
    assert result["remaining"] == 0
    assert result["limit"] == 10
    assert result["reason"] == "daily_limit_reached"
    assert result["upgrade_required"] is True


def test_active_trial_is_unlimited():
    tenant = {"id": "t3", "email": "user@example.com", "plan": "trial",
              "trial_end_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
              "usage_date": TODAY, "usage_tickets_today": 9999}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.can_process_ticket("t3")
    assert result["allowed"] is True
    assert result["plan"] == "trial"


def test_expired_trial_auto_downgrades_and_enforces_free_limit():
    tenant = {"id": "t4", "email": "user@example.com", "plan": "trial",
              "trial_end_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
              "usage_date": TODAY, "usage_tickets_today": 3}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.can_process_ticket("t4")
    assert result["plan"] == "free"
    assert result["allowed"] is True
    assert result["remaining"] == 7
    # The expiry check must have actually persisted the downgrade, not just
    # computed it in memory.
    assert tenant["plan"] == "free"


def test_active_paid_plan_within_30_days_is_not_downgraded():
    tenant = {"id": "t6", "email": "user@example.com", "plan": "starter",
              "plan_activated_at": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
              "usage_date": TODAY, "usage_tickets_today": 0}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.can_process_ticket("t6")
    assert result["plan"] == "starter"
    assert tenant["plan"] == "starter"


def test_paid_plan_past_30_days_is_downgraded_to_free_and_persisted():
    tenant = {"id": "t7", "email": "user@example.com", "plan": "starter",
              "plan_activated_at": (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),
              "usage_date": TODAY, "usage_tickets_today": 0}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.can_process_ticket("t7")
    assert result["plan"] == "free"
    assert tenant["plan"] == "free"
    # plan_activated_at must survive the downgrade — it's how the frontend
    # tells "this tenant's paid plan lapsed" apart from "never paid".
    assert tenant["plan_activated_at"] is not None


def test_paid_plan_with_no_activation_date_never_expires():
    """Legacy rows (plan set directly, no plan_activated_at) shouldn't get
    silently downgraded just because the column is empty."""
    tenant = {"id": "t8", "email": "user@example.com", "plan": "enterprise",
              "usage_date": TODAY, "usage_tickets_today": 0}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.can_process_ticket("t8")
    assert result["plan"] == "enterprise"


def test_usage_summary_flags_previously_paid_tenant_whose_plan_lapsed():
    tenant = {"id": "t9", "email": "user@example.com", "plan": "growth",
              "plan_activated_at": (datetime.now(timezone.utc) - timedelta(days=45)).isoformat(),
              "usage_date": TODAY}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        summary = ps.get_usage_summary("t9")
    assert summary["plan"] == "free"
    assert summary["was_previously_paid"] is True


def test_usage_summary_does_not_flag_a_tenant_who_never_paid():
    tenant = {"id": "t10", "email": "user@example.com", "plan": "free", "usage_date": TODAY}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        summary = ps.get_usage_summary("t10")
    assert summary["was_previously_paid"] is False


def test_usage_summary_reports_days_remaining_for_active_paid_plan():
    tenant = {"id": "t11", "email": "user@example.com", "plan": "starter",
              "plan_activated_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
              "usage_date": TODAY}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        summary = ps.get_usage_summary("t11")
    assert summary["plan"] == "starter"
    assert summary["plan_days_remaining"] == 25


def test_usage_resets_lazily_on_a_new_day():
    tenant = {"id": "t5", "email": "user@example.com", "plan": "free",
              "usage_date": "2000-01-01", "usage_tickets_today": 10}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.can_process_ticket("t5")
    assert result["allowed"] is True
    assert result["used"] == 0
    assert tenant["usage_date"] == TODAY


def test_check_brand_limit_blocks_second_brand_on_free_plan():
    tenant = {"id": "t6", "email": "user@example.com", "plan": "free"}

    def fake_select(table, params=None):
        if table == "tenants":
            return [tenant]
        if table == "brands":
            return [{"id": "b1"}]  # already has 1 brand
        return []

    with patch("src.services.plan_service.supabase_select", side_effect=fake_select):
        result = ps.check_brand_limit("t6")
    assert result["allowed"] is False
    assert result["limit"] == 1
    assert result["used"] == 1
