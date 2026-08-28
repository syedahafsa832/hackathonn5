"""
Manual paid-upgrade-request admin email.

Integrated straight into the existing manual bank-transfer/payment flow
(POST /api/v2/upgrade-requests, src/api/routes/upgrade_requests.py) - no new
upgrade flow, no polling, no AI. Fires once, right after the request row is
successfully persisted; a failure sending the notification must never turn
the merchant's already-successful request into a failed one.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes.upgrade_requests import router as upgrade_router  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402
from src.services import admin_alert_service  # noqa: E402

TENANT_ID = "tenant-aaaa"
TENANT_EMAIL = "merchant@example.com"


def _build_app():
    app = FastAPI()
    app.include_router(upgrade_router, prefix="/api/v2")
    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(tenant_id=TENANT_ID, email=TENANT_EMAIL)
    return app


def _post_upgrade_request(app, **overrides):
    body = {
        "name": "Jane Merchant",
        "email": TENANT_EMAIL,
        "brand": "Jane's Boutique",
        "plan": "growth",
        "transaction_reference": "TXN-12345",
    }
    body.update(overrides)
    client = TestClient(app)
    return client.post("/api/v2/upgrade-requests", json=body)


def _mock_supabase(tenant_row=None):
    def fake_insert(table, data):
        assert table == "upgrade_requests"
        return {**data, "id": "req-abc-123"}

    def fake_select(table, params=None):
        if table == "tenants":
            return [tenant_row] if tenant_row else []
        return []

    return fake_insert, fake_select


def test_successful_upgrade_request_sends_admin_email():
    app = _build_app()
    fake_insert, fake_select = _mock_supabase(tenant_row={"plan": "starter", "is_active": True})

    with patch("src.api.routes.upgrade_requests.supabase_insert", side_effect=fake_insert), \
         patch("src.api.routes.upgrade_requests.supabase_select", side_effect=fake_select), \
         patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        resp = _post_upgrade_request(app)

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    mock_send.assert_called_once()
    to_email, subject, body = mock_send.call_args[0]
    assert to_email == admin_alert_service.ADMIN_ALERT_EMAIL
    assert subject == "\U0001F4B0 NEW UPGRADE REQUEST — growth"
    assert body  # non-empty


def test_email_contains_requested_plan_and_merchant_account_details():
    app = _build_app()
    fake_insert, fake_select = _mock_supabase(tenant_row={"plan": "starter", "is_active": True})

    with patch("src.api.routes.upgrade_requests.supabase_insert", side_effect=fake_insert), \
         patch("src.api.routes.upgrade_requests.supabase_select", side_effect=fake_select), \
         patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        _post_upgrade_request(app, name="Jane Merchant", plan="enterprise", transaction_reference="TXN-99999")

    _, subject, body = mock_send.call_args[0]
    assert "enterprise" in subject
    assert "Jane Merchant" in body
    assert TENANT_EMAIL in body
    assert TENANT_ID in body
    assert "starter" in body  # current plan
    assert "enterprise" in body  # requested plan
    assert "req-abc-123" in body  # request id
    assert "TXN-99999" in body  # transaction reference
    assert "active" in body.lower()  # account status
    assert "ACTION REQUIRED" in body


def test_admin_notification_failure_does_not_fail_the_upgrade_request():
    app = _build_app()
    fake_insert, fake_select = _mock_supabase(tenant_row={"plan": "starter", "is_active": True})

    with patch("src.api.routes.upgrade_requests.supabase_insert", side_effect=fake_insert), \
         patch("src.api.routes.upgrade_requests.supabase_select", side_effect=fake_select), \
         patch("src.services.admin_alert_service.send_admin_notification", side_effect=RuntimeError("Resend down")):
        resp = _post_upgrade_request(app)

    # The merchant's request is still a success even though the admin
    # notification itself blew up.
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_tenant_lookup_failure_does_not_fail_the_upgrade_request():
    """If resolving the current plan for the notification fails (a DB blip),
    the request must still succeed - the notification content just degrades
    to 'unknown' for that one field."""
    app = _build_app()
    fake_insert, _ = _mock_supabase()

    def broken_select(table, params=None):
        raise RuntimeError("supabase unreachable")

    with patch("src.api.routes.upgrade_requests.supabase_insert", side_effect=fake_insert), \
         patch("src.api.routes.upgrade_requests.supabase_select", side_effect=broken_select), \
         patch("src.services.admin_alert_service.send_admin_notification") as mock_send:
        resp = _post_upgrade_request(app)

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    mock_send.assert_called_once()
    _, _, body = mock_send.call_args[0]
    assert "unknown" in body.lower()
