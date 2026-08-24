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
    """The approval-rate math bug: a real Shopify execution failure (human
    approved it, but the Shopify call itself failed) was previously
    excluded from BOTH the numerator and denominator - invisible to this
    metric entirely. It must now depress the approval rate, the same way a
    human rejection does. Checked via category_readiness (always present)
    rather than autopilot_readiness, since - separately - a sample with any
    failed_executions is "almost_there", not "ready_for_review", so
    autopilot_readiness (which now shares that exact same canonical status,
    see the contradictory-readiness fix below) is correctly None here."""
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

    body = resp.json()
    readiness = body["category_readiness"]["cancellation"]
    # 4 executed / (4 executed + 0 rejected + 1 failed) = 80% - not 100%,
    # and not simply omitted from the calculation as it was before.
    assert readiness["successful"] == 4
    assert readiness["failed_executions"] == 1
    assert readiness["approval_rate"] == 80.0
    assert body["autopilot_readiness"] is None


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

    readiness = resp.json()["category_readiness"]["cancellation"]
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


def test_autopilot_readiness_and_category_readiness_never_contradict_each_other():
    """Task 2 (production bug report): Automation said 'Almost there - not
    ready yet' while Customer Voice simultaneously said 'Luna is ready for
    Autopilot review' for the same brand, because autopilot_readiness
    (Customer Voice) was gated on sample size alone while category_readiness
    .cancellation.status (Automation) also accounted for failures. Locks in
    the fix directly: whenever there are execution failures in the sample,
    autopilot_readiness must be None (not "ready") at the exact same time
    category_readiness's status is "almost_there" (not "ready_for_review") -
    both pages read the one canonical status, so they can't diverge again."""
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

    body = resp.json()
    assert body["category_readiness"]["cancellation"]["status"] == "almost_there"
    assert body["autopilot_readiness"] is None  # never "ready for review" while Automation says "almost there"


# ── category_readiness (Automation page) ────────────────────────────────────

def test_category_readiness_present_even_below_minimum_sample():
    """Unlike autopilot_readiness (omitted below the sample floor),
    category_readiness must always be present - the "why isn't this ready
    yet" UX needs the real numbers even when there aren't enough yet."""
    all_cancel_actions = [
        {"id": "a1", "action_type": "cancel_order", "status": "executed", "created_at": _now_iso()},
        {"id": "a2", "action_type": "cancel_order", "status": "executed", "created_at": _now_iso()},
    ]

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return all_cancel_actions
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    cancellation = resp.json()["category_readiness"]["cancellation"]
    assert cancellation["status"] == "not_ready"
    assert cancellation["total_requests"] == 2
    assert cancellation["successful"] == 2


def test_category_readiness_ready_for_review_with_no_failures():
    all_cancel_actions = (
        [{"id": f"a{i}", "action_type": "cancel_order", "status": "executed", "created_at": _now_iso()} for i in range(5)]
    )

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return all_cancel_actions
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    cancellation = resp.json()["category_readiness"]["cancellation"]
    assert cancellation["status"] == "ready_for_review"
    assert cancellation["failed_executions"] == 0


def test_recent_failures_lists_real_failed_actions_with_category_and_reason():
    all_cancel_actions = [
        {"id": "a1", "action_type": "cancel_order", "status": "executed", "order_id": "1005"},
        {"id": "a2", "action_type": "cancel_order", "status": "failed", "order_id": "1009",
         "error_message": "This order has already been cancelled.", "updated_at": "2026-08-21T10:50:56Z"},
        {"id": "a3", "action_type": "cancel_order", "status": "failed", "order_id": "1008",
         "error_message": "Invalid Shopify access token. Please reconnect your store.", "updated_at": "2026-08-21T09:56:50Z"},
        {"id": "a4", "action_type": "cancel_order", "status": "failed", "order_id": "1011",
         "error_message": "No Shopify store connected.", "updated_at": "2026-08-13T02:18:27Z"},
    ]

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return all_cancel_actions
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    cancellation = resp.json()["category_readiness"]["cancellation"]
    # Readiness math is untouched by this feature.
    assert cancellation["failed_executions"] == 3
    assert cancellation["total_requests"] == 4

    failures = cancellation["recent_failures"]
    assert len(failures) == 3
    by_order = {f["order_id"]: f for f in failures}
    assert by_order["1009"]["category"] == "Order state changed"
    assert by_order["1009"]["reason"] == "This order has already been cancelled."
    assert by_order["1008"]["category"] == "Connection issue"
    assert by_order["1011"]["category"] == "Connection issue"
    assert all(f["status"] == "failed" for f in failures)
    # Most recent first.
    assert [f["order_id"] for f in failures] == ["1009", "1008", "1011"]


def test_recent_failures_empty_when_no_failures_exist():
    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return [{"id": "a1", "action_type": "cancel_order", "status": "executed", "order_id": "1005"}]
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    assert resp.json()["category_readiness"]["cancellation"]["recent_failures"] == []


def test_category_readiness_almost_there_when_failures_present_even_with_enough_sample():
    """The exact Phase 4 distinction: enough sample, but a real Shopify
    execution failure in the mix means "almost there," not "ready" -
    escalated (human rejections) alone don't trigger this, only failed."""
    all_cancel_actions = (
        [{"id": f"a{i}", "action_type": "cancel_order", "status": "executed", "created_at": _now_iso()} for i in range(5)]
        + [{"id": "f1", "action_type": "cancel_order", "status": "failed", "created_at": _now_iso()}]
    )

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return all_cancel_actions
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    cancellation = resp.json()["category_readiness"]["cancellation"]
    assert cancellation["status"] == "almost_there"
    assert cancellation["failed_executions"] == 1


def test_category_readiness_escalated_field_is_human_rejections_not_failures():
    """Rejections are normal, expected human judgment calls, not a red flag
    - a sample with rejections but zero execution failures still reads as
    "ready_for_review", matching the task's own worked example (47 requests,
    45 successful, 2 escalated, still "Ready for review")."""
    all_cancel_actions = (
        [{"id": f"a{i}", "action_type": "cancel_order", "status": "executed", "created_at": _now_iso()} for i in range(45)]
        + [{"id": f"r{i}", "action_type": "cancel_order", "status": "rejected", "created_at": _now_iso()} for i in range(2)]
    )

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return all_cancel_actions
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    cancellation = resp.json()["category_readiness"]["cancellation"]
    assert cancellation["escalated"] == 2
    assert cancellation["total_requests"] == 47
    assert cancellation["approval_rate"] == 95.7
    assert cancellation["status"] == "ready_for_review"


def test_category_readiness_cancellation_is_not_contaminated_by_refund_actions():
    """Different action categories must not contaminate each other's
    metrics - refund actions in the same brand's history must not count
    toward (or degrade) the cancellation readiness numbers. The fake here
    honors the real action_type filter the app sends (rather than ignoring
    it and returning everything), so this actually exercises the app's own
    filtering instead of just asserting on unfiltered fixture data."""
    mixed_actions = (
        [{"id": f"c{i}", "action_type": "cancel_order", "status": "executed", "created_at": _now_iso()} for i in range(5)]
        + [{"id": f"r{i}", "action_type": "refund", "status": "failed", "created_at": _now_iso()} for i in range(10)]
    )

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            if params and params.get("action_type"):
                wanted = params["action_type"].replace("eq.", "")
                return [a for a in mixed_actions if a["action_type"] == wanted]
            return mixed_actions
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    cancellation = resp.json()["category_readiness"]["cancellation"]
    # The refund failures must not leak into cancellation's counts or status.
    assert cancellation["total_requests"] == 5
    assert cancellation["failed_executions"] == 0
    assert cancellation["status"] == "ready_for_review"


def test_analytics_404s_for_a_brand_owned_by_another_tenant():
    def fake_select(table, params=None):
        return []  # brands query finds nothing for this tenant

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    assert resp.status_code == 404


def test_analytics_endpoint_never_writes_or_calls_shopify():
    """Autopilot Readiness (this task) is read-only aggregation only - no
    automatic Shopify mutation, no action-state write, must ever originate
    from this endpoint. Patches supabase_update/supabase_insert and
    ShopifyClient with a hard failure so the test would blow up loudly if
    any code path here ever tried to write or call Shopify."""
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

    def _forbidden(*args, **kwargs):
        raise AssertionError("analytics endpoint must never write or call Shopify")

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_update", side_effect=_forbidden), \
         patch("src.api.routes.v2_brands.supabase_insert", side_effect=_forbidden), \
         patch("src.services.shopify_service.ShopifyClient.__init__", side_effect=_forbidden):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    assert resp.status_code == 200
