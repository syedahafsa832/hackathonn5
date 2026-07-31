"""
Conversations List Fixes (item 9)
====================================
Covers GET /api/tickets (tickets.py, the endpoint the Conversations page
actually calls): confirms last_message is now populated from the same
normalized-message parser the ticket detail view uses, and that results
across merged brands remain sorted newest-first by updated_at.
"""
import os
import sys
from unittest.mock import patch, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.api.routes.tickets import router as tickets_router, _tickets_cache  # noqa: E402
from src.services.auth_service import auth_service as real_auth_service  # noqa: E402

app = FastAPI()
app.include_router(tickets_router, prefix="/api")
client = TestClient(app)

TENANT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
BRAND_ID = "ffffffff-1111-2222-3333-444444444444"


@pytest.fixture(autouse=True)
def _clear_cache():
    _tickets_cache.clear()
    yield
    _tickets_cache.clear()


def _ticket(id_, updated_at, messages=None, message=None):
    return {
        "id": id_, "brand_id": BRAND_ID, "status": "open",
        "updated_at": updated_at, "created_at": updated_at,
        "customer_email": "c@example.com",
        "messages": messages or [],
        "message": message,
    }


def test_last_message_populated_from_messages_array():
    tickets = [_ticket("t1", "2026-01-01T00:00:00Z", messages=[
        {"from": "Customer", "body": "Where is my order?", "direction": "inbound"},
        {"from": "AI Agent", "body": "It shipped yesterday!", "direction": "outbound"},
    ])]

    with patch("src.api.routes.tickets.supabase_select", return_value=[{"id": BRAND_ID, "tenant_id": TENANT_ID}]), \
         patch("src.services.supabase_service.supabase_service.get_tickets", new=AsyncMock(return_value=tickets)):
        token = real_auth_service.create_access_token(TENANT_ID, "owner@example.com")
        resp = client.get("/api/tickets", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data[0]["last_message"] == "It shipped yesterday!"


def test_last_message_falls_back_to_flat_message_field_for_old_tickets():
    tickets = [_ticket("t1", "2026-01-01T00:00:00Z", messages=[], message="Old-style ticket body")]

    with patch("src.api.routes.tickets.supabase_select", return_value=[{"id": BRAND_ID, "tenant_id": TENANT_ID}]), \
         patch("src.services.supabase_service.supabase_service.get_tickets", new=AsyncMock(return_value=tickets)):
        token = real_auth_service.create_access_token(TENANT_ID, "owner@example.com")
        resp = client.get("/api/tickets", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["last_message"] == "Old-style ticket body"


def test_merged_multi_brand_results_sorted_newest_first():
    brand_2 = "99999999-8888-7777-6666-555555555555"

    def fake_get_tickets(store_id=None, status=None):
        if store_id == BRAND_ID:
            return [_ticket("old", "2026-01-01T00:00:00Z")]
        if store_id == brand_2:
            return [_ticket("new", "2026-06-01T00:00:00Z")]
        return []

    with patch("src.api.routes.tickets.supabase_select",
               return_value=[{"id": BRAND_ID, "tenant_id": TENANT_ID}, {"id": brand_2, "tenant_id": TENANT_ID}]), \
         patch("src.services.supabase_service.supabase_service.get_tickets", new=AsyncMock(side_effect=fake_get_tickets)):
        token = real_auth_service.create_access_token(TENANT_ID, "owner@example.com")
        resp = client.get("/api/tickets", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    ids = [t["id"] for t in resp.json()]
    assert ids == ["new", "old"]
