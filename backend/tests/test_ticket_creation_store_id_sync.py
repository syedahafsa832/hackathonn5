"""
Manual ticket creation (POST /api/v2/tickets) writes store_id to match
brand_id (brand_id/store_id canonical-column audit finding).

tickets.py's list_tickets — the endpoint the dashboard's Conversations
page actually calls — filters exclusively by store_id, never brand_id.
create_ticket() here only wrote brand_id, so a manually-created ticket
was inserted successfully but never appeared in that list for anyone
(a visibility bug, not a cross-tenant exposure — the ticket wasn't
misattributed, it was just invisible everywhere). Fixed by writing both,
mirroring the same dual-write already used in v2_chat_widget.py.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes.v2_tickets import router as v2_tickets_router  # noqa: E402
from src.api.middleware.auth_middleware import require_agent_or_admin, AuthenticatedContext  # noqa: E402
from src.services.supabase_auth_service import UserContext, UserRole  # noqa: E402

app = FastAPI()
app.include_router(v2_tickets_router, prefix="/api/v2")

BRAND_ID = "brand-own-1"

app.dependency_overrides[require_agent_or_admin] = lambda: AuthenticatedContext(
    user=UserContext(
        user_id="user-1", supabase_auth_id="sb-1", organization_id="org-1",
        email="agent@example.com", role=UserRole.ADMIN,
    ),
    brand_ids=[BRAND_ID],
)
client = TestClient(app)


def test_manually_created_ticket_has_store_id_matching_brand_id():
    inserted = {}

    def fake_insert(table, data):
        row = {**data, "id": "ticket-new-1"}
        if table == "tickets":
            inserted["ticket"] = row
        return row

    with patch("src.api.routes.v2_tickets.supabase_insert", side_effect=fake_insert):
        resp = client.post("/api/v2/tickets", json={
            "brand_id": BRAND_ID,
            "customer_email": "customer@example.com",
            "customer_name": "Customer",
            "subject": "Where is my order?",
            "message": "Hi, checking on my order status.",
            "channel": "manual",
        })

    assert resp.status_code == 200, resp.text
    assert inserted["ticket"]["brand_id"] == BRAND_ID
    assert inserted["ticket"]["store_id"] == BRAND_ID
