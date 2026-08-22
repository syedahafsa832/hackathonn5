"""
Customer Voice feedback summary + Resolution Analytics / Autopilot
Readiness (Phase 3). Every number these two endpoints return must be a
real aggregation over the brand's own tickets/actions/chat_feedback -
these tests check the aggregation math itself, the tenant scoping, and
that a metric with no underlying data is omitted (None) rather than
guessed.
"""
import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import v2_brands  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402

app = FastAPI()
app.include_router(v2_brands.router, prefix="/api/v2")
client = TestClient(app)

BRAND = {"id": "brand-1", "tenant_id": "tenant-1", "name": "Test Brand"}


def _override_tenant(tenant_id="tenant-1"):
    async def _dep():
        return TenantContext(tenant_id=tenant_id, email="merchant@example.com")
    return _dep


def _with_tenant(fn):
    app.dependency_overrides[get_current_tenant] = _override_tenant()
    try:
        return fn()
    finally:
        app.dependency_overrides.clear()


# ── GET /{brand_id}/feedback — summary + channel ─────────────────────────

def test_feedback_summary_computes_average_total_and_breakdown():
    feedback_rows = [
        {"id": "f1", "brand_id": "brand-1", "rating_stars": 5, "ticket_id": "t1"},
        {"id": "f2", "brand_id": "brand-1", "rating_stars": 5, "ticket_id": "t2"},
        {"id": "f3", "brand_id": "brand-1", "rating_stars": 4, "ticket_id": None},
        {"id": "f4", "brand_id": "brand-1", "rating_stars": 1, "ticket_id": "t1"},
    ]
    tickets = [{"id": "t1", "channel": "chat"}, {"id": "t2", "channel": "email"}]

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "chat_feedback":
            return feedback_rows
        if table == "tickets":
            return tickets
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/feedback"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total"] == 4
    assert body["summary"]["average"] == 3.8  # (5+5+4+1)/4
    assert body["summary"]["breakdown"] == {"1": 1, "2": 0, "3": 0, "4": 1, "5": 2}
    by_id = {f["id"]: f["channel"] for f in body["feedback"]}
    assert by_id["f1"] == "chat"
    assert by_id["f2"] == "email"
    assert by_id["f3"] == "unknown"  # no ticket_id -> can't resolve a channel, never guessed


def test_feedback_summary_is_none_when_no_feedback_exists():
    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/feedback"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] is None
    assert body["feedback"] == []


# ── GET /{brand_id}/analytics ─────────────────────────────────────────────

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def test_analytics_counts_conversations_resolved_and_escalated():
    tickets = [
        {"id": "t1", "status": "auto_resolved", "channel": "chat", "created_at": _now_iso()},
        {"id": "t2", "status": "auto_resolved", "channel": "chat", "created_at": _now_iso()},
        {"id": "t3", "status": "escalated", "channel": "email", "created_at": _now_iso()},
        {"id": "t4", "status": "open", "channel": "chat", "created_at": _now_iso()},
    ]

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "tickets":
            return tickets
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    body = resp.json()
    assert body["conversations_handled"] == 4
    assert body["resolved_by_luna"] == 2
    assert body["escalated_to_human"] == 1


def test_analytics_approval_rate_computed_from_executed_and_rejected_actions():
    actions = [
        {"id": "a1", "action_type": "refund", "status": "executed", "created_at": _now_iso()},
        {"id": "a2", "action_type": "refund", "status": "executed", "created_at": _now_iso()},
        {"id": "a3", "action_type": "cancel_order", "status": "rejected", "created_at": _now_iso()},
        {"id": "a4", "action_type": "cancel_order", "status": "pending", "created_at": _now_iso()},
    ]

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return actions
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    body = resp.json()
    # 2 executed / (2 executed + 1 rejected) = 66.7% - pending is not counted either way
    assert body["approval_rate"] == 66.7
    assert body["cancellation_count"] == 2
    assert body["refund_count"] == 2


def test_analytics_approval_rate_is_none_with_no_decided_actions():
    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    body = resp.json()
    assert body["approval_rate"] is None
    assert body["avg_response_time_seconds"] is None
    assert body["csat"] is None
    assert body["autopilot_readiness"] is None


def test_autopilot_readiness_omitted_below_minimum_sample():
    all_cancel_actions = [
        {"id": "a1", "action_type": "cancel_order", "status": "executed", "created_at": _now_iso()},
        {"id": "a2", "action_type": "cancel_order", "status": "executed", "created_at": _now_iso()},
    ]

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions" and params.get("action_type") == "eq.cancel_order":
            return all_cancel_actions
        if table == "actions":
            return all_cancel_actions
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    assert resp.json()["autopilot_readiness"] is None


def test_autopilot_readiness_shown_at_or_above_minimum_sample():
    all_cancel_actions = [
        {"id": f"a{i}", "action_type": "cancel_order", "status": "executed", "created_at": _now_iso()}
        for i in range(4)
    ] + [{"id": "a5", "action_type": "cancel_order", "status": "rejected", "created_at": _now_iso()}]

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return all_cancel_actions
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    readiness = resp.json()["autopilot_readiness"]
    assert readiness is not None
    assert readiness["eligible_cancellations"] == 4
    assert readiness["approval_rate"] == 80.0


def test_autopilot_readiness_successful_executions_count_correctly():
    """Baseline: with zero rejections and zero failures, a clean run of
    executed cancellations is 100% - the "successes count correctly" case
    the denominator fix must not disturb."""
    all_cancel_actions = [
        {"id": f"a{i}", "action_type": "cancel_order", "status": "executed", "created_at": _now_iso()}
        for i in range(5)
    ]

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return all_cancel_actions
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    readiness = resp.json()["autopilot_readiness"]
    assert readiness["eligible_cancellations"] == 5
    assert readiness["failed_executions"] == 0
    assert readiness["approval_rate"] == 100.0


def test_autopilot_readiness_failed_executions_count_against_the_rate():
    """The bug: a real Shopify execution failure (human approved it, but
    the Shopify call itself failed) was previously excluded from BOTH the
    numerator and denominator - invisible to this metric entirely. It must
    now depress the approval rate, the same way a human rejection does."""
    all_cancel_actions = (
        [{"id": f"a{i}", "action_type": "cancel_order", "status": "executed", "created_at": _now_iso()} for i in range(4)]
        + [{"id": "a5", "action_type": "cancel_order", "status": "failed", "created_at": _now_iso()}]
    )

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return all_cancel_actions
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    readiness = resp.json()["autopilot_readiness"]
    assert readiness is not None
    # 4 executed / (4 executed + 0 rejected + 1 failed) = 80% - not 100%,
    # and not simply omitted from the calculation as it was before.
    assert readiness["eligible_cancellations"] == 4
    assert readiness["failed_executions"] == 1
    assert readiness["approval_rate"] == 80.0


def test_autopilot_readiness_failures_cannot_artificially_inflate_the_rate():
    """A pathological case proving failures are counted as a negative
    signal, never a neutral or positive one: many executions, but half of
    all decided actions failed - the rate must reflect that, not read as a
    misleadingly high 100% because failures were dropped from the math."""
    all_cancel_actions = (
        [{"id": f"ok{i}", "action_type": "cancel_order", "status": "executed", "created_at": _now_iso()} for i in range(5)]
        + [{"id": f"fail{i}", "action_type": "cancel_order", "status": "failed", "created_at": _now_iso()} for i in range(5)]
    )

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return all_cancel_actions
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    readiness = resp.json()["autopilot_readiness"]
    assert readiness["approval_rate"] == 50.0
    assert readiness["approval_rate"] != 100.0


def test_autopilot_readiness_insufficient_sample_still_blocks_even_with_failures_present():
    """Failures count toward the sample size too (they're real historical
    data points), but a handful of actions - decided or not - still isn't
    enough evidence either way; the card must stay omitted below the
    minimum, exactly as it already does for a small all-executed sample."""
    all_cancel_actions = [
        {"id": "a1", "action_type": "cancel_order", "status": "executed", "created_at": _now_iso()},
        {"id": "a2", "action_type": "cancel_order", "status": "failed", "created_at": _now_iso()},
    ]

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return all_cancel_actions
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    assert resp.json()["autopilot_readiness"] is None


def test_analytics_404s_for_a_brand_owned_by_another_tenant():
    def fake_select(table, params=None):
        return []  # brands query finds nothing for this tenant

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    assert resp.status_code == 404
