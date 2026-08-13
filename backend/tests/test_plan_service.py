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
    """plan_days_remaining = (activated_at + 30days - now()).days, which
    floors any partial day. Computing plan_activated_at from a live
    datetime.now() and then calling get_usage_summary() a moment later left
    a real (if tiny) gap between the two `now()` calls, so this test's
    expected day count depended on exactly how much wall-clock time elapsed
    between them - it could floor to 24 or 25 depending on machine/test
    speed. Freezing plan_service's clock to a single fixed instant removes
    that gap entirely so the 5-day-elapsed -> 25-days-remaining math is
    exact and reproducible."""
    frozen_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now

    tenant = {"id": "t11", "email": "user@example.com", "plan": "starter",
              "plan_activated_at": (frozen_now - timedelta(days=5)).isoformat(),
              "usage_date": frozen_now.date().isoformat()}
    p1, p2 = _mocked(tenant)
    with p1, p2, patch("src.services.plan_service.datetime", _FrozenDateTime):
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


def test_every_plan_caps_brands_at_one():
    """tResolv is single-store-per-account — Growth used to allow 3 and
    Scale/enterprise was uncapped (None). If either regresses back to
    allowing more than one brand, this must fail loudly rather than
    silently reopening multi-brand signups on a paid plan."""
    for plan_id, limits in ps.PLAN_LIMITS.items():
        assert limits.get("brands") == 1, f"plan '{plan_id}' allows {limits.get('brands')} brands, expected 1"


def test_check_brand_limit_blocks_second_brand_on_growth_plan():
    """Growth previously allowed up to 3 brands — confirms the cap now
    applies uniformly across plans, not just free."""
    tenant = {"id": "t7", "email": "user@example.com", "plan": "growth"}

    def fake_select(table, params=None):
        if table == "tenants":
            return [tenant]
        if table == "brands":
            return [{"id": "b1"}]  # already has 1 brand
        return []

    with patch("src.services.plan_service.supabase_select", side_effect=fake_select):
        result = ps.check_brand_limit("t7")
    assert result["allowed"] is False
    assert result["limit"] == 1


# ── AI-reply quota: trial's lifetime-total counter ───────────────────────────
# Trial no longer gates on a daily ai_replies_per_day allowance (500/day,
# never the effective bottleneck since tickets_per_day was unlimited) — it's
# now a real lifetime total (25 across the whole 14-day trial), read directly
# off tenants.ai_replies_used_total with no reset logic at all.

def test_trial_ai_replies_lifetime_mode_blocks_at_25():
    tenant = {"id": "t12", "email": "user@example.com", "plan": "trial",
              "trial_end_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
              "ai_replies_used_total": 25}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.check_limit("t12", "ai_replies")
    assert result["allowed"] is False
    assert result["limit"] == 25
    assert result["used"] == 25
    assert result["reason"] == "trial_limit_reached"


def test_trial_ai_replies_lifetime_mode_allows_below_25():
    tenant = {"id": "t13", "email": "user@example.com", "plan": "trial",
              "trial_end_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
              "ai_replies_used_total": 24}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.check_limit("t13", "ai_replies")
    assert result["allowed"] is True
    assert result["remaining"] == 1


def test_trial_ai_replies_ignores_stale_daily_usage_date():
    """A stale usage_date must not matter for the lifetime counter — unlike
    the daily-reset resources, ai_replies_total never resets."""
    tenant = {"id": "t14", "email": "user@example.com", "plan": "trial",
              "trial_end_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
              "usage_date": "2000-01-01", "ai_replies_used_total": 25}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.check_limit("t14", "ai_replies")
    assert result["allowed"] is False
    assert result["used"] == 25


def test_record_usage_increments_lifetime_counter_for_trial():
    tenant = {"id": "t15", "email": "user@example.com", "plan": "trial",
              "trial_end_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
              "ai_replies_used_total": 5}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        ps.record_usage("t15", "ai_replies")
    assert tenant["ai_replies_used_total"] == 6
    # Must not touch the daily column at all.
    assert tenant.get("usage_ai_replies_today") in (None, 0)


def test_free_plan_ai_replies_still_uses_daily_column_not_lifetime():
    """Regression guard: the new lifetime branch is trial-only — free plan's
    ai_replies_per_day daily cap must be completely unaffected, even with a
    huge (irrelevant) ai_replies_used_total sitting on the row."""
    tenant = {"id": "t16", "email": "user@example.com", "plan": "free",
              "usage_date": TODAY, "usage_ai_replies_today": 9, "ai_replies_used_total": 999}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.check_limit("t16", "ai_replies")
    assert result["allowed"] is True  # 9 < 10, free plan's daily cap
    assert result["used"] == 9
    assert result["limit"] == 10


def test_record_ai_reply_event_inserts_row_and_increments_counter():
    tenant = {"id": "t17", "email": "user@example.com", "plan": "trial",
              "trial_end_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
              "ai_replies_used_total": 0}
    inserted = []

    def fake_select(table, params=None):
        return [tenant] if table == "tenants" else []

    def fake_update(table, match, data):
        tenant.update(data)
        return [tenant]

    def fake_insert(table, data):
        inserted.append((table, data))
        return data

    with patch("src.services.plan_service.supabase_select", side_effect=fake_select), \
         patch("src.services.plan_service.supabase_update", side_effect=fake_update), \
         patch("src.services.plan_service.supabase_insert", side_effect=fake_insert):
        ps.record_ai_reply_event(
            "t17", channel="gmail", brand_id="b1", ticket_id="tk1",
            customer_identifier="cust@example.com", model_used="mistral-large-latest",
        )

    assert tenant["ai_replies_used_total"] == 1
    assert len(inserted) == 1
    table, data = inserted[0]
    assert table == "ai_reply_events"
    assert data["tenant_id"] == "t17"
    assert data["channel"] == "gmail"
    assert data["brand_id"] == "b1"
    assert data["model_used"] == "mistral-large-latest"


def test_cross_channel_calls_share_one_quota_and_block_at_25_total():
    """Abuse-prevention proof: switching channels does not grant extra quota.
    15 Gmail-channel events + 10 Chat-Widget-channel events against the same
    tenant must exhaust the 25-total cap jointly, and a 26th call on EITHER
    channel must then be blocked — the cap is per-tenant, not per-channel."""
    tenant = {"id": "t18", "email": "user@example.com", "plan": "trial",
              "trial_end_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
              "ai_replies_used_total": 0}
    inserted = []

    def fake_select(table, params=None):
        return [tenant] if table == "tenants" else []

    def fake_update(table, match, data):
        tenant.update(data)
        return [tenant]

    def fake_insert(table, data):
        inserted.append(data)
        return data

    with patch("src.services.plan_service.supabase_select", side_effect=fake_select), \
         patch("src.services.plan_service.supabase_update", side_effect=fake_update), \
         patch("src.services.plan_service.supabase_insert", side_effect=fake_insert):

        for _ in range(15):
            assert ps.check_limit("t18", "ai_replies")["allowed"] is True
            ps.record_ai_reply_event("t18", channel="gmail")

        for _ in range(10):
            assert ps.check_limit("t18", "ai_replies")["allowed"] is True
            ps.record_ai_reply_event("t18", channel="chat_widget")

        assert tenant["ai_replies_used_total"] == 25

        assert ps.check_limit("t18", "ai_replies")["allowed"] is False  # gmail's next attempt
        assert ps.check_limit("t18", "ai_replies")["allowed"] is False  # chat_widget's next attempt

    gmail_events = [e for e in inserted if e["channel"] == "gmail"]
    chat_events = [e for e in inserted if e["channel"] == "chat_widget"]
    assert len(gmail_events) == 15
    assert len(chat_events) == 10


def test_usage_summary_exposes_trial_ai_reply_fields_and_breakdown():
    tenant = {"id": "t19", "email": "user@example.com", "plan": "trial",
              "trial_end_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
              "ai_replies_used_total": 18, "usage_date": TODAY}

    def fake_select(table, params=None):
        if table == "tenants":
            return [tenant]
        if table == "ai_reply_events":
            return [{"channel": "gmail"}] * 12 + [{"channel": "chat_widget"}] * 6
        return []

    with patch("src.services.plan_service.supabase_select", side_effect=fake_select):
        summary = ps.get_usage_summary("t19")

    assert summary["ai_replies_used_trial"] == 18
    assert summary["ai_replies_trial_limit"] == 25
    assert summary["ai_replies_trial_remaining"] == 7
    assert summary["trial_expired"] is False
    assert summary["ai_replies_breakdown"] == {"gmail": 12, "chat_widget": 6}
    assert summary["upgrade_required"] is False


def test_usage_summary_upgrade_required_when_trial_quota_exhausted():
    tenant = {"id": "t21", "email": "user@example.com", "plan": "trial",
              "trial_end_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
              "ai_replies_used_total": 25, "usage_date": TODAY}

    def fake_select(table, params=None):
        return [tenant] if table == "tenants" else []

    with patch("src.services.plan_service.supabase_select", side_effect=fake_select):
        summary = ps.get_usage_summary("t21")

    assert summary["ai_replies_trial_remaining"] == 0
    assert summary["upgrade_required"] is True


def test_usage_summary_trial_expired_flag_independent_of_current_plan():
    """trial_end_at in the past + plan already auto-downgraded to 'free' by
    _resolve_plan() — trial_expired must still read True, so the frontend can
    tell this apart from a tenant that was always on Free."""
    tenant = {"id": "t20", "email": "user@example.com", "plan": "trial",
              "trial_end_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
              "ai_replies_used_total": 3, "usage_date": TODAY}

    def fake_select(table, params=None):
        return [tenant] if table == "tenants" else []

    def fake_update(table, match, data):
        tenant.update(data)
        return [tenant]

    with patch("src.services.plan_service.supabase_select", side_effect=fake_select), \
         patch("src.services.plan_service.supabase_update", side_effect=fake_update):
        summary = ps.get_usage_summary("t20")

    assert summary["plan"] == "free"  # auto-downgraded
    assert summary["trial_expired"] is True
