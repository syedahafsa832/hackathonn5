"""
Human Mode persistence/display bug (ticket #4a641e86).

Root cause: conversation_overrides.active is the correct, authoritative
"is a human handling this" signal - message_processor.py's
_check_thread_override() already reads it correctly. But the dashboard
(TicketDetail.jsx) derived its own "AI is handling this" banner purely from
tickets.status === 'human_managing', and send-reply (tickets.py) legitimately
overwrites tickets.status to "resolved" on every manual send - which flipped
the banner back to "AI is handling this" even though the override was never
deactivated and the AI was, correctly, still gated off.

Fix: GET /tickets/{id} now also returns human_override_active, computed from
conversation_overrides (not from status). TicketDetail.jsx checks that field
first. send-reply's existing behavior (touches tickets.* only, never
conversation_overrides) is unchanged - these tests lock that in as a
regression guard rather than as new behavior.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402
from src.api.routes.admin import router as admin_router  # noqa: E402
from src.api.routes.tickets import router as tickets_router  # noqa: E402
from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


TICKET_ID = "4a641e86-40d6-480e-aadf-b1d0e50d7322"
TENANT_ID = "tenant-1"
BRAND_ID = "brand-1"


def _app():
    app = FastAPI()
    app.include_router(admin_router)
    app.include_router(tickets_router, prefix="/api")  # tickets_router already has its own "/tickets" prefix

    def fake_tenant():
        return TenantContext(tenant_id=TENANT_ID, email="agent@example.com")

    app.dependency_overrides[get_current_tenant] = fake_tenant
    return app


def _ticket(**overrides):
    t = {
        "id": TICKET_ID, "brand_id": BRAND_ID, "store_id": BRAND_ID,
        "status": "human_managing", "customer_email": "customer@example.com",
        "subject": "hi", "messages": [], "ai_draft": None, "ai_reply": None,
    }
    t.update(overrides)
    return t


def test_takeover_creates_active_override_and_sets_status():
    app = _app()
    client = TestClient(app)
    with patch("src.api.routes.admin.supabase_service.get_ticket_by_id", new=AsyncMock(return_value=_ticket())), \
         patch("src.api.routes.admin._get_tenant_brand_ids", new=AsyncMock(return_value=[BRAND_ID])), \
         patch("src.api.routes.admin.supabase_insert") as mock_insert, \
         patch("src.api.routes.admin.supabase_service.update_ticket", new=AsyncMock()) as mock_update, \
         patch("src.api.routes.admin.supabase_service.log_audit", new=AsyncMock()):
        resp = client.post(f"/tickets/{TICKET_ID}/takeover", json={}, headers={"Authorization": "Bearer test"})

    assert resp.status_code == 200
    insert_call = mock_insert.call_args
    assert insert_call.args[0] == "conversation_overrides"
    assert insert_call.args[1]["conversation_id"] == TICKET_ID
    assert insert_call.args[1]["active"] is True
    mock_update.assert_awaited_once_with(TICKET_ID, {"status": "human_managing"})


def test_send_reply_never_touches_conversation_overrides():
    app = _app()
    client = TestClient(app)
    ticket = _ticket(status="human_managing")
    with patch("src.api.routes.tickets._assert_ticket_access", new=AsyncMock(return_value=ticket)), \
         patch("src.api.routes.tickets.supabase_select", return_value=[{"id": BRAND_ID, "gmail_connected": True}]), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=AsyncMock(return_value={"success": True})), \
         patch("src.api.routes.tickets.supabase_update") as mock_update:
        resp = client.post(f"/api/tickets/{TICKET_ID}/send-reply", json={"body": "HI"}, headers={"Authorization": "Bearer test"})

    assert resp.status_code == 200
    # tickets.status legitimately moves to "resolved" - that's real ticket-
    # lifecycle behavior, not what's under test here.
    tables_written = [c.args[0] for c in mock_update.call_args_list]
    assert tables_written == ["tickets", "tickets"]
    assert "conversation_overrides" not in tables_written


def test_get_ticket_reports_human_handled_from_override_even_when_status_is_resolved():
    """The exact bug: status flipped to 'resolved' by send-reply, but the
    override is still active - GET must still report human_override_active."""
    app = _app()
    client = TestClient(app)
    resolved_ticket = _ticket(status="resolved", email_sent=True)
    with patch("src.api.routes.tickets.supabase_service.get_ticket_by_id", new=AsyncMock(return_value=resolved_ticket)), \
         patch("src.api.routes.tickets.supabase_service.check_conversation_override", new=AsyncMock(return_value=True)), \
         patch("src.api.routes.tickets._get_tenant_brand_ids", new=AsyncMock(return_value=[BRAND_ID])):
        resp = client.get(f"/api/tickets/{TICKET_ID}", headers={"Authorization": "Bearer test"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["human_override_active"] is True


def test_get_ticket_reports_no_override_when_none_is_active():
    app = _app()
    client = TestClient(app)
    with patch("src.api.routes.tickets.supabase_service.get_ticket_by_id", new=AsyncMock(return_value=_ticket(status="open"))), \
         patch("src.api.routes.tickets.supabase_service.check_conversation_override", new=AsyncMock(return_value=False)), \
         patch("src.api.routes.tickets._get_tenant_brand_ids", new=AsyncMock(return_value=[BRAND_ID])):
        resp = client.get(f"/api/tickets/{TICKET_ID}", headers={"Authorization": "Bearer test"})

    assert resp.status_code == 200
    assert resp.json()["human_override_active"] is False


def test_ai_reply_still_gated_off_while_override_remains_active_after_manual_reply():
    """A subsequent inbound customer message on the same thread must not
    trigger an AI auto-reply while the override is active - unaffected by
    this fix (message_processor.py wasn't touched), locked in as a
    regression guard in this exact bug's context."""
    proc = UnifiedMessageProcessor()
    active_override = {"conversation_id": TICKET_ID, "active": True, "overridden_by": "agent@example.com"}
    matching_ticket = _ticket(status="resolved", customer_email="customer@example.com", subject="hi")

    with patch("src.workers.message_processor.supabase_select", return_value=[active_override]), \
         patch("src.workers.message_processor.supabase_service.get_ticket_by_id", new=AsyncMock(return_value=matching_ticket)):
        is_overridden = run(proc._check_thread_override("customer@example.com", "hi"))

    assert is_overridden is True
