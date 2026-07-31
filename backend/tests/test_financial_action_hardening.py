"""
Financial Action Hardening Tests (H2)
========================================
Covers financial_audit.py (idempotency-key cache lookup, append-only audit
recording, per-org rate limiting) and its wiring into execute_cancel_order /
execute_refund in v2_tickets.py: a retried request with the same
Idempotency-Key header replays the original result instead of calling
Shopify again, and an org over the 10/minute limit gets 429.

All Shopify/Gmail/Supabase calls are mocked — no live services required.
"""
import os
import sys
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("ENCRYPTION_SECRET", "test-encryption-secret-do-not-use-in-prod")

import src.services.financial_audit as financial_audit  # noqa: E402
from src.services.auth_service import auth_service as real_auth_service  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_rate_buckets():
    financial_audit._rate_buckets.clear()
    yield
    financial_audit._rate_buckets.clear()


# ─── 1. financial_audit.py pure-function unit tests ──────────────────────────

def test_get_cached_result_returns_none_when_no_key():
    assert financial_audit.get_cached_result("t1", "refund", None) is None


def test_get_cached_result_returns_the_stored_row():
    with patch.object(financial_audit, "supabase_select", return_value=[{"status": "success", "result": {"ok": True}}]) as mock_select:
        found = financial_audit.get_cached_result("ticket-1", "refund", "key-abc")
    assert found == {"status": "success", "result": {"ok": True}}
    params = mock_select.call_args[0][1]
    assert params["ticket_id"] == "eq.ticket-1"
    assert params["action_type"] == "eq.refund"
    assert params["idempotency_key"] == "eq.key-abc"


def test_record_financial_action_inserts_expected_fields():
    with patch.object(financial_audit, "supabase_insert") as mock_insert:
        financial_audit.record_financial_action(
            ticket_id="t1", tenant_id="tenant-1", brand_id="brand-1",
            action_type="cancel_order", idempotency_key="key-1",
            user_id="user-1", user_email="a@example.com", ip_address="1.2.3.4",
            status="success", result={"success": True},
        )
    table, data = mock_insert.call_args[0]
    assert table == "financial_action_audit_log"
    assert data["ticket_id"] == "t1"
    assert data["action_type"] == "cancel_order"
    assert data["ip_address"] == "1.2.3.4"
    assert data["status"] == "success"


def test_record_financial_action_swallows_duplicate_key_race_silently():
    """A concurrent duplicate insert hitting the unique index is the
    idempotency guarantee working, not an error the caller should see."""
    with patch.object(financial_audit, "supabase_insert", side_effect=Exception("409 Conflict: 23505 duplicate key")):
        financial_audit.record_financial_action(
            ticket_id="t1", tenant_id="t", brand_id="b", action_type="refund",
            idempotency_key="dupe", user_id="u", user_email="e", ip_address="1.1.1.1",
            status="success", result={},
        )  # must not raise


def test_org_rate_limit_allows_ten_then_blocks_the_eleventh():
    org = "org-under-test"
    for i in range(10):
        assert financial_audit.check_org_rate_limit(org) is True
    assert financial_audit.check_org_rate_limit(org) is False


def test_org_rate_limit_is_scoped_per_org():
    for _ in range(10):
        assert financial_audit.check_org_rate_limit("org-a") is True
    assert financial_audit.check_org_rate_limit("org-a") is False
    # A different org must be unaffected by org-a's exhausted bucket.
    assert financial_audit.check_org_rate_limit("org-b") is True


def test_org_rate_limit_resets_after_the_window_expires():
    org = "org-window-test"
    for _ in range(10):
        assert financial_audit.check_org_rate_limit(org) is True
    assert financial_audit.check_org_rate_limit(org) is False
    # Simulate the 60s window having passed.
    financial_audit._rate_buckets[org] = [t - 61 for t in financial_audit._rate_buckets[org]]
    assert financial_audit.check_org_rate_limit(org) is True


# ─── 2. Route-level: idempotency replay and rate limiting ───────────────────

app = FastAPI()
from src.api.routes.v2_tickets import router as v2_tickets_router  # noqa: E402
app.include_router(v2_tickets_router, prefix="/api/v2")
client = TestClient(app)

TENANT_ID = "33333333-3333-3333-3333-333333333333"
BRAND_ID = "44444444-4444-4444-4444-444444444444"
TICKET_ID = "55555555-5555-5555-5555-555555555555"


def _brand():
    return {
        "id": BRAND_ID, "tenant_id": TENANT_ID, "name": "Test Brand",
        "shopify_connected": True, "shopify_access_token": "encrypted-token-placeholder",
        "shopify_domain": "test.myshopify.com", "gmail_connected": False,
    }


def _ticket(status="open"):
    return {
        "id": TICKET_ID, "brand_id": BRAND_ID, "status": status,
        "detected_order_id": "1001", "customer_email": "cust@example.com",
        "customer_name": "Cust", "messages": [], "subject": "Cancel my order",
    }


def _fake_select_factory(ticket_status_holder):
    def fake_select(table, params=None):
        params = params or {}
        if table == "users":
            return []
        if table == "tenants":
            return [{"id": TENANT_ID, "email": "owner@example.com", "is_active": True}]
        if table == "brands":
            if "tenant_id" in params:
                return [_brand()]
            if "gmail_connected" in params:
                return []
            return [_brand()]
        if table == "tickets":
            return [_ticket(ticket_status_holder["status"])]
        if table == "actions":
            return []
        return []
    return fake_select


def _fake_update_factory(ticket_status_holder):
    def fake_update(table, match, data):
        if table == "tickets":
            if "status" in data:
                ticket_status_holder["status"] = data["status"]
            return [{**_ticket(ticket_status_holder["status"])}]
        return [data]
    return fake_update


def test_cancel_order_retry_with_same_idempotency_key_does_not_call_shopify_twice():
    token = real_auth_service.create_access_token(TENANT_ID, "owner@example.com")
    ticket_status = {"status": "open"}

    audit_rows = []

    def fake_audit_select(table, params=None):
        matches = [r for r in audit_rows if all(
            r.get(k) == (v[3:] if isinstance(v, str) and v.startswith("eq.") else v)
            for k, v in (params or {}).items() if k in ("ticket_id", "action_type", "idempotency_key")
        )]
        return matches

    def fake_audit_insert(table, data):
        row = dict(data)
        audit_rows.append(row)
        return row

    with patch("src.api.routes.v2_tickets.supabase_select", side_effect=_fake_select_factory(ticket_status)), \
         patch("src.api.routes.v2_tickets.supabase_update", side_effect=_fake_update_factory(ticket_status)), \
         patch("src.services.supabase_auth_service.supabase_select", side_effect=_fake_select_factory(ticket_status)), \
         patch("src.api.middleware.auth_middleware.supabase_auth_service.verify_jwt",
               side_effect=real_auth_service.decode_token), \
         patch("src.services.financial_audit.supabase_select", side_effect=fake_audit_select), \
         patch("src.services.financial_audit.supabase_insert", side_effect=fake_audit_insert), \
         patch("src.services.plan_service.check_limit", return_value={"allowed": True}), \
         patch("src.services.plan_service.record_shopify_action"), \
         patch("src.services.shopify_service.ShopifyClient.cancel_order",
               new=AsyncMock(return_value={"success": True, "order_name": "#1001"})) as mock_cancel, \
         patch("src.services.shopify_service.decrypt_token", return_value="plain-token"):

        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "retry-key-123"}

        first = client.post(f"/api/v2/tickets/{TICKET_ID}/actions/cancel", headers=headers)
        assert first.status_code == 200, first.text
        assert mock_cancel.call_count == 1

        # Simulate the ticket having moved on (as it would after a real
        # cancel) — a naive re-check-by-status would now 409. The
        # idempotency-key replay must short-circuit before that.
        second = client.post(f"/api/v2/tickets/{TICKET_ID}/actions/cancel", headers=headers)
        assert second.status_code == 200, second.text
        assert mock_cancel.call_count == 1  # NOT called again
        assert second.json() == first.json()


def test_financial_action_endpoint_rate_limited_at_eleventh_request_per_org():
    token = real_auth_service.create_access_token(TENANT_ID, "owner@example.com")
    ticket_status = {"status": "open"}

    with patch("src.api.routes.v2_tickets.supabase_select", side_effect=_fake_select_factory(ticket_status)), \
         patch("src.api.routes.v2_tickets.supabase_update", side_effect=_fake_update_factory(ticket_status)), \
         patch("src.services.supabase_auth_service.supabase_select", side_effect=_fake_select_factory(ticket_status)), \
         patch("src.api.middleware.auth_middleware.supabase_auth_service.verify_jwt",
               side_effect=real_auth_service.decode_token), \
         patch("src.services.financial_audit.supabase_select", return_value=[]), \
         patch("src.services.financial_audit.supabase_insert", return_value={}), \
         patch("src.services.plan_service.check_limit", return_value={"allowed": True}), \
         patch("src.services.plan_service.record_shopify_action"), \
         patch("src.services.shopify_service.ShopifyClient.cancel_order",
               new=AsyncMock(return_value={"success": True, "order_name": "#1001"})), \
         patch("src.services.shopify_service.decrypt_token", return_value="plain-token"):

        headers = {"Authorization": f"Bearer {token}"}
        statuses = []
        for i in range(11):
            # Distinct idempotency keys so each one actually attempts execution
            # (not short-circuited by a cache hit) and hits the rate limiter.
            r = client.post(
                f"/api/v2/tickets/{TICKET_ID}/actions/cancel",
                headers={**headers, "Idempotency-Key": f"k-{i}"},
            )
            statuses.append(r.status_code)
            ticket_status["status"] = "open"  # reset so the claim step doesn't 409 first

        assert statuses[:10] == [200] * 10
        assert statuses[10] == 429
