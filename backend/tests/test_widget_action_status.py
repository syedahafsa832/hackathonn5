"""
GET /api/v2/widget/actions/{action_id}/status — the poll target the chat
widget uses to learn a staged cancellation's real outcome (executed/failed)
after a merchant approves/rejects it via the dashboard, so ActionResultCard
can show a confirmed "Order cancelled" state instead of leaving "Awaiting
merchant approval" up forever. Public/unauthenticated like the rest of this
router (action_id is an unguessable UUID) - these tests cover the field
allowlist (never leak customer_email/reason/risk_* from the same `actions`
row) and the not-found/rate-limit edges.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from unittest.mock import patch  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import v2_chat_widget  # noqa: E402

app = FastAPI()
app.include_router(v2_chat_widget.router, prefix="/api/v2")
client = TestClient(app)


def setup_function():
    v2_chat_widget._rate_buckets.clear()


FULL_ACTION_ROW = {
    "id": "action-1",
    "status": "executed",
    "action_type": "cancel_order",
    "order_number": "1013",
    "error_message": None,
    # Sensitive fields that must never reach the customer-facing widget:
    "customer_email": "jane@example.com",
    "customer_name": "Jane Doe",
    "reason": "customer changed their mind",
    "ai_reasoning": "high confidence cancellation intent",
    "risk_level": "low",
    "risk_factors": [],
    "original_message": "please cancel order 1013",
}


def test_status_returns_only_allowlisted_fields_for_executed_action():
    with patch("src.api.routes.v2_chat_widget.supabase_select", return_value=[FULL_ACTION_ROW]):
        resp = client.get("/api/v2/widget/actions/action-1/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "status": "executed",
        "action_type": "cancel_order",
        "order_number": "1013",
        "error_message": None,
    }
    for leaked in ("customer_email", "customer_name", "reason", "ai_reasoning", "risk_level", "original_message"):
        assert leaked not in body


def test_status_reports_pending_before_merchant_acts():
    pending_row = {**FULL_ACTION_ROW, "status": "pending", "error_message": None}
    with patch("src.api.routes.v2_chat_widget.supabase_select", return_value=[pending_row]):
        resp = client.get("/api/v2/widget/actions/action-1/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


def test_status_reports_failed_with_generic_error_message():
    failed_row = {**FULL_ACTION_ROW, "status": "failed", "error_message": "Shopify order already cancelled"}
    with patch("src.api.routes.v2_chat_widget.supabase_select", return_value=[failed_row]):
        resp = client.get("/api/v2/widget/actions/action-1/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error_message"] == "Shopify order already cancelled"


def test_status_404_for_unknown_action_id():
    with patch("src.api.routes.v2_chat_widget.supabase_select", return_value=[]):
        resp = client.get("/api/v2/widget/actions/does-not-exist/status")
    assert resp.status_code == 404


def test_status_rate_limited_after_max_requests_from_same_ip():
    with patch("src.api.routes.v2_chat_widget.supabase_select", return_value=[FULL_ACTION_ROW]):
        for _ in range(v2_chat_widget._RATE_MAX):
            resp = client.get("/api/v2/widget/actions/action-1/status")
            assert resp.status_code == 200
        resp = client.get("/api/v2/widget/actions/action-1/status")
    assert resp.status_code == 429
