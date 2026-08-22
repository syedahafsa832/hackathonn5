"""
Post-ticket review system (Phase 2).

chat_feedback existed live in Supabase with no ticket_id/brand_id — a
merchant had no tenant-scoped way to see their own customers' feedback.
These tests cover:
1. POST /api/v2/widget/feedback now enriches the insert with ticket_id/
   brand_id looked up from the session, and sanitizes/caps feedback_text.
2. GET /api/v2/brands/{brand_id}/feedback (dashboard-facing) is scoped to
   the caller's own tenant via the same _get_owned_brand() ownership check
   every other brand-scoped endpoint in v2_brands.py already uses.
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
from src.api.routes import v2_brands  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402

widget_app = FastAPI()
widget_app.include_router(v2_chat_widget.router, prefix="/api/v2")
widget_client = TestClient(widget_app)

brands_app = FastAPI()
brands_app.include_router(v2_brands.router, prefix="/api/v2")
brands_client = TestClient(brands_app)

TICKET_ROW = {"id": "ticket-1", "brand_id": "brand-1", "channel": "chat", "gmail_thread_id": "sess-1"}


def setup_function():
    v2_chat_widget._rate_buckets.clear()


# ── POST /widget/feedback ────────────────────────────────────────────────

def test_feedback_insert_enriches_with_ticket_and_brand_id():
    captured = {}

    def fake_select(table, params=None):
        if table == "tickets":
            return [TICKET_ROW]
        return []

    def fake_insert(table, data):
        captured["table"] = table
        captured["data"] = data
        return data

    with patch("src.api.routes.v2_chat_widget.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_chat_widget.supabase_insert", side_effect=fake_insert):
        resp = widget_client.post("/api/v2/widget/feedback", json={
            "session_id": "sess-1", "rating": "positive", "feedback_text": "Luna fixed it in seconds!",
        })

    assert resp.status_code == 200
    assert captured["table"] == "chat_feedback"
    assert captured["data"]["ticket_id"] == "ticket-1"
    assert captured["data"]["brand_id"] == "brand-1"
    assert captured["data"]["feedback_text"] == "Luna fixed it in seconds!"


def test_feedback_text_is_sanitized_and_length_capped():
    captured = {}

    def fake_select(table, params=None):
        return [TICKET_ROW] if table == "tickets" else []

    def fake_insert(table, data):
        captured["data"] = data
        return data

    long_text = "a" * 2000
    with patch("src.api.routes.v2_chat_widget.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_chat_widget.supabase_insert", side_effect=fake_insert):
        resp = widget_client.post("/api/v2/widget/feedback", json={
            "session_id": "sess-1", "rating": "negative",
            "feedback_text": f"<script>alert(1)</script>{long_text}",
        })

    assert resp.status_code == 200
    assert "<script>" not in captured["data"]["feedback_text"]
    assert len(captured["data"]["feedback_text"]) <= 1000


def test_feedback_without_matching_ticket_still_stores_rating():
    captured = {}

    def fake_insert(table, data):
        captured["data"] = data
        return data

    with patch("src.api.routes.v2_chat_widget.supabase_select", return_value=[]), \
         patch("src.api.routes.v2_chat_widget.supabase_insert", side_effect=fake_insert):
        resp = widget_client.post("/api/v2/widget/feedback", json={"session_id": "sess-unknown", "rating": "positive"})

    assert resp.status_code == 200
    assert captured["data"]["ticket_id"] is None
    assert captured["data"]["brand_id"] is None


def test_feedback_rejects_invalid_rating():
    resp = widget_client.post("/api/v2/widget/feedback", json={"session_id": "sess-1", "rating": "meh"})
    assert resp.status_code == 400


# ── GET /brands/{brand_id}/feedback (dashboard, tenant-scoped) ──────────

def _override_tenant(tenant_id: str):
    async def _dep():
        return TenantContext(tenant_id=tenant_id, email="merchant@example.com")
    return _dep


def test_brand_feedback_is_scoped_to_owning_tenant():
    brand_row = {"id": "brand-1", "tenant_id": "tenant-1", "name": "Test Brand"}
    feedback_rows = [{"id": "fb-1", "brand_id": "brand-1", "rating": "positive", "feedback_text": "Great!"}]

    def fake_select(table, params=None):
        if table == "brands":
            return [brand_row]
        if table == "chat_feedback":
            return feedback_rows
        return []

    brands_app.dependency_overrides[get_current_tenant] = _override_tenant("tenant-1")
    try:
        with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
            resp = brands_client.get("/api/v2/brands/brand-1/feedback")
    finally:
        brands_app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["feedback"] == feedback_rows


def test_brand_feedback_404s_for_a_brand_owned_by_another_tenant():
    def fake_select(table, params=None):
        # brands query filters by tenant_id server-side (_get_owned_brand) —
        # a brand-1/tenant-2 lookup for tenant-1 correctly returns nothing.
        if table == "brands":
            return []
        return []

    brands_app.dependency_overrides[get_current_tenant] = _override_tenant("tenant-1")
    try:
        with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
            resp = brands_client.get("/api/v2/brands/brand-1/feedback")
    finally:
        brands_app.dependency_overrides.clear()

    assert resp.status_code == 404
