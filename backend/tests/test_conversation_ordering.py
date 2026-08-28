"""
Conversations ordering (migration 056): the live list endpoint the
dashboard actually calls is GET /api/tickets (tickets.py, mounted at
/api - not v2_tickets.py, which the frontend never calls). Its multi-brand
merge path used to sort purely by updated_at, which changes on any write to
the row - not just a new customer message. Now sorts by
last_customer_message_at first, falling back to updated_at only for legacy/
non-email rows where it's null.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes.tickets import router as tickets_router, _tickets_cache  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402


def setup_function():
    # list_tickets() caches its response per tenant:status:store_id for
    # _TICKETS_CACHE_TTL seconds - every test here uses the same tenant, so
    # without clearing this, later tests would see an earlier test's cached
    # result instead of exercising the sort logic at all.
    _tickets_cache.clear()


def _app():
    app = FastAPI()
    app.include_router(tickets_router, prefix="/api")
    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(tenant_id="tenant-1", email="owner@example.com")
    return app


def _ticket(id_, **overrides):
    t = {"id": id_, "channel": "email", "messages": []}
    t.update(overrides)
    return t


def test_new_customer_reply_moves_conversation_to_top():
    """An old conversation (stale updated_at from internal writes) with a
    genuinely NEWER last_customer_message_at must outrank a conversation
    whose updated_at is more recent but whose customer hasn't written back."""
    old_but_recently_touched = _ticket(
        "stale", last_customer_message_at="2026-08-27T16:43:00+00:00",
        updated_at="2026-08-28T03:57:00+00:00",  # internal touch, no new customer message
    )
    fresh_customer_reply = _ticket(
        "fresh", last_customer_message_at="2026-08-28T07:41:00+00:00",
        updated_at="2026-08-28T07:41:05+00:00",
    )

    with patch("src.api.routes.tickets._get_tenant_brand_ids", new=AsyncMock(return_value=["brand-1"])), \
         patch("src.api.routes.tickets.supabase_service.get_tickets", new=AsyncMock(return_value=[old_but_recently_touched, fresh_customer_reply])), \
         patch("src.api.routes.v2_tickets._normalize_ticket_messages", return_value=[]):
        client = TestClient(_app())
        resp = client.get("/api/tickets")

    ids = [t["id"] for t in resp.json()]
    assert ids == ["fresh", "stale"]


def test_internal_update_does_not_move_an_old_ticket_above_newer_customer_activity():
    """Same scenario, phrased the other way: the stale ticket's more recent
    updated_at must NOT let it outrank the genuinely newer customer reply."""
    stale = _ticket("stale", last_customer_message_at="2026-08-01T00:00:00+00:00", updated_at="2026-08-28T12:00:00+00:00")
    newer = _ticket("newer", last_customer_message_at="2026-08-20T00:00:00+00:00", updated_at="2026-08-20T00:05:00+00:00")

    with patch("src.api.routes.tickets._get_tenant_brand_ids", new=AsyncMock(return_value=["brand-1"])), \
         patch("src.api.routes.tickets.supabase_service.get_tickets", new=AsyncMock(return_value=[stale, newer])), \
         patch("src.api.routes.v2_tickets._normalize_ticket_messages", return_value=[]):
        client = TestClient(_app())
        resp = client.get("/api/tickets")

    ids = [t["id"] for t in resp.json()]
    assert ids == ["newer", "stale"]


def test_legacy_ticket_with_null_last_customer_message_at_falls_back_to_updated_at():
    legacy = _ticket("legacy", last_customer_message_at=None, updated_at="2026-08-25T00:00:00+00:00")
    with_field = _ticket("has-field", last_customer_message_at="2026-08-20T00:00:00+00:00", updated_at="2026-08-20T00:00:00+00:00")

    with patch("src.api.routes.tickets._get_tenant_brand_ids", new=AsyncMock(return_value=["brand-1"])), \
         patch("src.api.routes.tickets.supabase_service.get_tickets", new=AsyncMock(return_value=[legacy, with_field])), \
         patch("src.api.routes.v2_tickets._normalize_ticket_messages", return_value=[]):
        client = TestClient(_app())
        resp = client.get("/api/tickets")

    # Legacy ticket is still returned (not dropped) and still orders using
    # its own updated_at as a fallback, not excluded from the list.
    ids = {t["id"] for t in resp.json()}
    assert ids == {"legacy", "has-field"}
