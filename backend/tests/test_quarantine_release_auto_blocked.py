"""
Merchant-visible gap: status="auto_blocked" (a confident noise verdict -
see email_guardian_service.py's _create_quarantine_record docstring) was
designed to never appear in the merchant's review queue at all - the
dashboard's Quarantine page only ever fetched status=pending, and
POST /quarantine/{id}/promote rejected any record not already "pending".
A real customer email misclassified as outreach/spam had zero visibility
and zero recourse - confirmed live for a test email quarantined as
"outreach" at 0.8 confidence with no way to release it.

Fix: GET /quarantine now serves ?status=auto_blocked like any other status
(dashboard adds a "Blocked" tab), and POST /quarantine/{id}/promote now
accepts a starting status of "pending" OR "auto_blocked" - every other
promote guarantee (atomic single-claim, gmail_message_id dedup against an
existing ticket) is unchanged and applies identically to both.
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
from src.api.routes.v2_quarantine import router as quarantine_router  # noqa: E402

TENANT_ID = "tenant-1"
BRAND_ID = "brand-1"
QID = "quar-1"


def _app():
    app = FastAPI()
    app.include_router(quarantine_router, prefix="/api/v1")
    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(tenant_id=TENANT_ID, email="agent@example.com")
    return app


def _blocked_row(**overrides):
    row = {
        "id": QID, "brand_id": BRAND_ID, "status": "auto_blocked",
        "sender_email": "maybe-a-customer@example.com", "subject": "hello",
        "body_preview": "hi there", "thread_id": "t1", "gmail_message_id": "msg-1",
        "ai_classification": "outreach", "ai_confidence": 0.8,
    }
    row.update(overrides)
    return row


def _brands_row(**overrides):
    row = {"id": BRAND_ID, "tenant_id": TENANT_ID, "is_active": True, "gmail_connected": True}
    row.update(overrides)
    return row


# 1. GET /quarantine?status=auto_blocked returns blocked rows (previously
#    only "pending" was ever queried by the dashboard).
def test_list_can_filter_to_auto_blocked_status():
    app = _app()
    client = TestClient(app)

    def fake_select(table, params=None):
        if table == "brands":
            return [_brands_row()]
        if table == "email_quarantine":
            if params.get("status") == "eq.auto_blocked":
                return [_blocked_row()]
            return []
        return []

    with patch("src.api.routes.v2_quarantine.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_quarantine.supabase_update", return_value=[]):
        resp = client.get("/api/v1/quarantine?status=auto_blocked&limit=50")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "auto_blocked"
    assert body["blocked"] == 1


# 2. Promoting a genuinely auto_blocked record now succeeds (used to 404).
def test_promote_releases_an_auto_blocked_record():
    app = _app()
    client = TestClient(app)
    mock_run = AsyncMock()

    def fake_select(table, params=None):
        if table == "brands":
            return [_brands_row()]
        if table == "email_quarantine":
            return [_blocked_row()]
        if table == "tickets":
            return []
        return []

    with patch("src.api.routes.v2_quarantine.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_quarantine.supabase_update", return_value=[{"id": QID}]) as mock_update, \
         patch("src.api.routes.v2_quarantine._run_promotion", new=mock_run):
        resp = client.post(f"/api/v1/quarantine/{QID}/promote")

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    mock_run.assert_awaited_once()
    # The atomic claim's WHERE clause must accept the record's actual
    # starting status (auto_blocked), not just "pending".
    claim_call = next(c for c in mock_update.call_args_list if c.args[2].get("status") == "promoted")
    assert claim_call.args[1]["status"] == "in.(pending,auto_blocked)"


# 3. A record that's already promoted/discarded/expired still can't be
#    re-promoted - the fix only widens acceptance to include auto_blocked,
#    it doesn't remove the guard entirely.
def test_promote_still_rejects_an_already_promoted_record():
    app = _app()
    client = TestClient(app)

    def fake_select(table, params=None):
        if table == "brands":
            return [_brands_row()]
        if table == "email_quarantine":
            return [_blocked_row(status="promoted")]
        return []

    with patch("src.api.routes.v2_quarantine.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_quarantine.supabase_update", return_value=[{"id": QID}]):
        resp = client.post(f"/api/v1/quarantine/{QID}/promote")

    assert resp.status_code == 404


# 4. Discard is unchanged - still pending-only, never accepts auto_blocked
#    (an already-blocked email has nothing to "discard" from a ticket queue
#    it was never in; only release-to-ticket makes sense for it).
def test_discard_still_rejects_an_auto_blocked_record():
    app = _app()
    client = TestClient(app)

    def fake_select(table, params=None):
        if table == "brands":
            return [_brands_row()]
        if table == "email_quarantine":
            return [_blocked_row()]
        return []

    with patch("src.api.routes.v2_quarantine.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_quarantine.supabase_update", return_value=[{"id": QID}]):
        resp = client.post(f"/api/v1/quarantine/{QID}/discard")

    assert resp.status_code == 404
