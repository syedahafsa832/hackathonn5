"""
send-reply (tickets.py) previously only set human_approved=True for a
manually-edited reply - a plain "Approve" click on Luna's draft (no body)
never set it, so reply_style_service.count_eligible_approved_replies()
(human_approved.is.true OR human_response.not.is.null) could never count
the common case, leaving Reply Style stuck at "0/20" regardless of real
approval volume. Fix: human_approved=True is now set on every successful
send, whether approved-as-is or edited.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402
from src.api.routes.tickets import router as tickets_router  # noqa: E402

TICKET_ID = "4a641e86-40d6-480e-aadf-b1d0e50d7322"
TENANT_ID = "tenant-1"
BRAND_ID = "brand-1"


def _app():
    app = FastAPI()
    app.include_router(tickets_router, prefix="/api")

    def fake_tenant():
        return TenantContext(tenant_id=TENANT_ID, email="agent@example.com")

    app.dependency_overrides[get_current_tenant] = fake_tenant
    return app


def _ticket(**overrides):
    t = {
        "id": TICKET_ID, "brand_id": BRAND_ID, "store_id": BRAND_ID,
        "status": "open", "customer_email": "customer@example.com",
        "subject": "hi", "messages": [], "ai_draft": "Here is a draft reply.", "ai_reply": None,
    }
    t.update(overrides)
    return t


def _send(body_json):
    app = _app()
    client = TestClient(app)
    ticket = _ticket()
    with patch("src.api.routes.tickets._assert_ticket_access", new=AsyncMock(return_value=ticket)), \
         patch("src.api.routes.tickets.supabase_select", return_value=[{"id": BRAND_ID, "gmail_connected": True}]), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=AsyncMock(return_value={"success": True})), \
         patch("src.api.routes.tickets.supabase_update") as mock_update:
        resp = client.post(f"/api/tickets/{TICKET_ID}/send-reply", json=body_json, headers={"Authorization": "Bearer test"})
    return resp, mock_update


def test_approving_ai_draft_as_is_sets_human_approved():
    resp, mock_update = _send({})  # no body -> approve-as-is path
    assert resp.status_code == 200
    ticket_updates = [c.args[2] for c in mock_update.call_args_list if c.args[0] == "tickets"]
    status_update = next(u for u in ticket_updates if "status" in u)
    assert status_update["human_approved"] is True
    assert status_update.get("ai_reply") is not None
    assert "human_response" not in status_update


def test_manually_edited_reply_also_sets_human_approved():
    resp, mock_update = _send({"body": "A human-written reply."})
    assert resp.status_code == 200
    ticket_updates = [c.args[2] for c in mock_update.call_args_list if c.args[0] == "tickets"]
    status_update = next(u for u in ticket_updates if "status" in u)
    assert status_update["human_approved"] is True
    assert status_update.get("human_response") == "A human-written reply."


def test_approve_ai_alias_also_sets_human_approved():
    app = _app()
    client = TestClient(app)
    ticket = _ticket()
    with patch("src.api.routes.tickets._assert_ticket_access", new=AsyncMock(return_value=ticket)), \
         patch("src.api.routes.tickets.supabase_select", return_value=[{"id": BRAND_ID, "gmail_connected": True}]), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=AsyncMock(return_value={"success": True})), \
         patch("src.api.routes.tickets.supabase_update") as mock_update:
        resp = client.post(f"/api/tickets/{TICKET_ID}/approve-ai", headers={"Authorization": "Bearer test"})

    assert resp.status_code == 200
    ticket_updates = [c.args[2] for c in mock_update.call_args_list if c.args[0] == "tickets"]
    status_update = next(u for u in ticket_updates if "status" in u)
    assert status_update["human_approved"] is True
