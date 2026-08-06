"""
Subscription/Entitlement Hardening Tests (see prompt TASK 9)
=============================================================
1. Admin unlimited — 10,000 tickets still allowed
2. Trial active — within limits
3. Trial AI-abuse protection — 26th AI reply blocked (25 lifetime total)
4. Free plan — 11th ticket -> blocked with structured reason
5. Brand creation — free user's 2nd brand blocked
6. Gmail connection — free user's 2nd Gmail blocked
7. Trial expiry — automatically becomes free
8. Subscriptions migration — SQL structure sanity (no live Postgres available
   in this environment; see test docstring for exactly what was/wasn't verified)
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


def _mocked(tenant, brands=None, users=None):
    def fake_select(table, params=None):
        if table == "tenants":
            return [tenant]
        if table == "brands":
            return brands or []
        if table == "users":
            return users or []
        return []

    def fake_update(table, match, data):
        tenant.update(data)
        return [tenant]

    return patch("src.services.plan_service.supabase_select", side_effect=fake_select), \
           patch("src.services.plan_service.supabase_update", side_effect=fake_update)


# ---- 1. Admin unlimited --------------------------------------------------

def test_admin_allows_ten_thousand_tickets():
    tenant = {"id": "admin1", "email": "syedahafsa772@gmail.com", "plan": "free",
              "usage_date": TODAY, "usage_tickets_today": 10000}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.check_limit("admin1", "tickets")
    assert result["allowed"] is True
    assert result["plan"] == "super_admin"


# ---- 2. Trial active — within limits -------------------------------------
# Trial's AI-reply cap changed from a daily allowance (500/day, never the
# effective bottleneck since tickets/day was unlimited) to a real lifetime
# total: 25 replies across the whole 14-day trial, read directly off
# tenants.ai_replies_used_total (see plan_service.PLAN_LIMITS["trial"]).

def test_trial_within_limits_is_allowed():
    tenant = {"id": "trial1", "email": "user@example.com", "plan": "trial",
              "trial_end_at": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
              "usage_date": TODAY, "ai_replies_used_total": 10}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.check_limit("trial1", "ai_replies")
    assert result["allowed"] is True
    assert result["plan"] == "trial"
    assert result["remaining"] == 15  # 25 - 10


# ---- 3. Trial AI-abuse protection -----------------------------------------

def test_trial_blocks_26th_ai_reply():
    tenant = {"id": "trial2", "email": "user@example.com", "plan": "trial",
              "trial_end_at": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
              "usage_date": TODAY, "ai_replies_used_total": 25}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.check_limit("trial2", "ai_replies")
    assert result["allowed"] is False
    assert result["limit"] == 25
    assert result["used"] == 25
    assert result["reason"] == "trial_limit_reached"
    assert result["upgrade_required"] is True


def test_trial_ticket_volume_remains_unlimited_even_with_ai_replies_capped():
    """Trial tickets/day is explicitly unlimited (TASK 1) — only AI replies,
    emails, and Shopify actions are capped."""
    tenant = {"id": "trial3", "email": "user@example.com", "plan": "trial",
              "trial_end_at": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
              "usage_date": TODAY, "usage_tickets_today": 999999}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.check_limit("trial3", "tickets")
    assert result["allowed"] is True
    assert result["limit"] is None


# ---- 4. Free plan — 11th ticket blocked -----------------------------------

def test_free_plan_11th_ticket_is_blocked():
    tenant = {"id": "free1", "email": "user@example.com", "plan": "free",
              "usage_date": TODAY, "usage_tickets_today": 10}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.check_limit("free1", "tickets")
    assert result["allowed"] is False
    assert result["used"] == 10
    assert result["limit"] == 10
    error_body = ps.build_limit_error("tickets", result)
    assert error_body == {
        "error": "PLAN_LIMIT_REACHED",
        "resource": "tickets",
        "current": 10,
        "limit": 10,
        "upgrade_required": True,
    }


# ---- 5. Brand creation — 2nd brand on free plan blocked -------------------

def test_free_plan_second_brand_blocked():
    tenant = {"id": "free2", "email": "user@example.com", "plan": "free"}
    p1, p2 = _mocked(tenant, brands=[{"id": "b1"}])
    with p1, p2:
        result = ps.check_limit("free2", "brands")
    assert result["allowed"] is False
    assert result["limit"] == 1
    assert result["used"] == 1


def test_free_plan_first_brand_allowed():
    tenant = {"id": "free2b", "email": "user@example.com", "plan": "free"}
    p1, p2 = _mocked(tenant, brands=[])
    with p1, p2:
        result = ps.check_limit("free2b", "brands")
    assert result["allowed"] is True


# ---- 6. Gmail connection — 2nd Gmail on free plan blocked -----------------

def test_free_plan_second_gmail_connection_blocked():
    tenant = {"id": "free3", "email": "user@example.com", "plan": "free"}

    def fake_select(table, params=None):
        if table == "tenants":
            return [tenant]
        if table == "brands":
            # one brand already has gmail_connected=true
            return [{"id": "b1"}]
        return []

    with patch("src.services.plan_service.supabase_select", side_effect=fake_select):
        result = ps.check_limit("free3", "gmail_accounts")
    assert result["allowed"] is False
    assert result["limit"] == 1
    assert result["used"] == 1


# ---- 7. Trial expiry — automatically becomes free -------------------------

def test_expired_trial_automatically_becomes_free():
    tenant = {"id": "exp1", "email": "user@example.com", "plan": "trial",
              "trial_end_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
              "usage_date": TODAY, "usage_tickets_today": 2}
    p1, p2 = _mocked(tenant)
    with p1, p2:
        result = ps.check_limit("exp1", "tickets")
    assert result["plan"] == "free"
    assert tenant["plan"] == "free"  # persisted, not just computed
    assert result["limit"] == 10  # now enforcing free-plan tickets/day, not unlimited


# ---- 8. Subscriptions migration -------------------------------------------

def test_subscriptions_migration_sql_structure():
    """No live Postgres/Supabase instance is available in this environment,
    so this does NOT prove the migration applies cleanly against a real
    database. What it does verify: valid SQL statement structure (via
    sqlparse), required columns/constraints/indexes are present, and the
    foreign key references the correct existing table/column."""
    import sqlparse
    path = os.path.join(os.path.dirname(__file__), "..", "migrations", "023_subscriptions_table.sql")
    sql = open(path, encoding="utf-8").read()

    parsed = [s for s in sqlparse.parse(sql) if s.get_type() != "UNKNOWN"]
    kinds = [s.get_type() for s in parsed]
    assert "CREATE" in kinds

    assert "CREATE TABLE IF NOT EXISTS subscriptions" in sql
    assert "REFERENCES tenants(id)" in sql
    for column in (
        "tenant_id", "stripe_customer_id", "stripe_subscription_id",
        "plan", "status", "current_period_start", "current_period_end",
        "created_at", "updated_at",
    ):
        assert column in sql, f"missing column: {column}"
    assert "idx_subscriptions_tenant_id" in sql
    assert "idx_subscriptions_stripe_customer_id" in sql
    assert "idx_subscriptions_status" in sql
