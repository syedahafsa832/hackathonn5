"""
Cross-tenant isolation regression tests for the security-audit fixes made to
admin.py, brands.py, agentic.py, and events.py.

Before this fix, all of the following were reachable with NO tenant/brand
ownership check at all (several had no auth dependency whatsoever):
  - admin.py: gdpr_delete, export_data, get_retention/update_retention,
    get_audit_logs, sync_orders_from_shopify, send_draft
  - brands.py: test-connection, sync-products, sync-orders
  - agentic.py: get_ticket_analysis (GET /agentic/ticket/{id}), and
    process_ticket's order lookup (order_number is not globally unique
    across brands)
  - events.py: list_events (returned ALL tenants' tickets/actions when
    brand_id was omitted), get_event (no ownership check by id)

Each now requires `get_current_tenant` and verifies the resource's own
brand/tenant ownership before returning or mutating anything, using the
same `_get_tenant_brand_ids` / `_assert_owned` conventions already used
elsewhere in this codebase (see tickets.py, v2_brands.py).

Also covers: the legacy returns.py/actions.py routers were unmounted in
main.py (confirmed dead — zero live frontend callers — and structurally
unfixable without a schema change), and the `_get_tenant_brand_ids`
shopify_domain fallback (tickets.py) no longer returns a brand already
claimed by a different tenant.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes.admin import router as admin_router  # noqa: E402
from src.api.routes.brands import router as brands_router  # noqa: E402
from src.api.routes.agentic import router as agentic_router  # noqa: E402
from src.api.routes.events import router as events_router  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402

app = FastAPI()
app.include_router(admin_router, prefix="/api")
app.include_router(brands_router, prefix="/api")
app.include_router(agentic_router, prefix="/api")
app.include_router(events_router, prefix="/api")
client = TestClient(app)

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
BRAND_A = "brand-a"
BRAND_B = "brand-b"

TICKET_A = {"id": "ticket-a", "brand_id": BRAND_A, "store_id": BRAND_A, "customer_email": "a@example.com", "subject": "A issue"}
TICKET_B = {"id": "ticket-b", "brand_id": BRAND_B, "store_id": BRAND_B, "customer_email": "b@example.com", "subject": "B issue"}


def _brands_table(table, params=None):
    params = params or {}
    if table != "brands":
        return []
    rows = [{"id": BRAND_A, "tenant_id": TENANT_A, "is_active": True},
            {"id": BRAND_B, "tenant_id": TENANT_B, "is_active": True}]
    if "tenant_id" in params:
        wanted = params["tenant_id"].split("eq.")[-1]
        rows = [r for r in rows if r["tenant_id"] == wanted]
    if "id" in params:
        wanted = params["id"].split("eq.")[-1]
        rows = [r for r in rows if r["id"] == wanted]
    return rows


def _as_tenant_a():
    return TenantContext(tenant_id=TENANT_A, email="a@example.com")


def _as_tenant_b():
    return TenantContext(tenant_id=TENANT_B, email="b@example.com")


def teardown_function():
    app.dependency_overrides.clear()


# ─── admin.py ────────────────────────────────────────────────────────────────

def test_gdpr_delete_cross_tenant_denied():
    app.dependency_overrides[get_current_tenant] = _as_tenant_a
    with patch("src.api.routes.tickets.supabase_select", side_effect=_brands_table), \
         patch("src.services.supabase_service.supabase_service.delete_customer_data", new=AsyncMock()) as mock_del:
        resp = client.delete("/api/gdpr/delete", params={"email": "victim@example.com", "store_id": BRAND_B})
    assert resp.status_code == 404
    mock_del.assert_not_called()


def test_gdpr_delete_own_brand_allowed():
    app.dependency_overrides[get_current_tenant] = _as_tenant_a
    with patch("src.api.routes.tickets.supabase_select", side_effect=_brands_table), \
         patch("src.services.supabase_service.supabase_service.delete_customer_data", new=AsyncMock()) as mock_del, \
         patch("src.services.supabase_service.supabase_service.log_audit", new=AsyncMock()):
        resp = client.delete("/api/gdpr/delete", params={"email": "a@example.com", "store_id": BRAND_A})
    assert resp.status_code == 200
    mock_del.assert_called_once()


def test_export_data_cross_tenant_denied():
    app.dependency_overrides[get_current_tenant] = _as_tenant_a
    with patch("src.api.routes.tickets.supabase_select", side_effect=_brands_table), \
         patch("src.api.routes.admin.supabase_select") as mock_select:
        resp = client.get("/api/export", params={"store_id": BRAND_B})
    assert resp.status_code == 404
    mock_select.assert_not_called()


def test_audit_logs_cross_tenant_denied():
    app.dependency_overrides[get_current_tenant] = _as_tenant_a
    with patch("src.api.routes.tickets.supabase_select", side_effect=_brands_table):
        resp = client.get("/api/audit-logs", params={"store_id": BRAND_B})
    assert resp.status_code == 404


def test_sync_orders_cross_tenant_denied():
    app.dependency_overrides[get_current_tenant] = _as_tenant_a
    with patch("src.api.routes.tickets.supabase_select", side_effect=_brands_table):
        resp = client.post("/api/sync-orders", params={"store_id": BRAND_B})
    assert resp.status_code == 404


def test_send_draft_cross_tenant_denied():
    app.dependency_overrides[get_current_tenant] = _as_tenant_a
    with patch("src.api.routes.tickets.supabase_select", side_effect=_brands_table), \
         patch("src.services.supabase_service.supabase_service.get_ticket_by_id", new=AsyncMock(return_value=TICKET_B)):
        resp = client.post(f"/api/tickets/{TICKET_B['id']}/send-draft", json={})
    assert resp.status_code == 404


def test_send_draft_unauthenticated_rejected():
    resp = client.post(f"/api/tickets/{TICKET_A['id']}/send-draft", json={})
    assert resp.status_code == 401


# ─── brands.py ───────────────────────────────────────────────────────────────

def test_brand_test_connection_cross_tenant_denied():
    app.dependency_overrides[get_current_tenant] = _as_tenant_a
    with patch("src.api.routes.brands.supabase_select", side_effect=_brands_table), \
         patch("src.services.brand_manager.brand_manager.test_connection", new=AsyncMock()) as mock_test:
        resp = client.post(f"/api/brands/{BRAND_B}/test-connection")
    assert resp.status_code == 404
    mock_test.assert_not_called()


def test_brand_sync_products_unauthenticated_rejected():
    resp = client.post(f"/api/brands/{BRAND_A}/sync-products")
    assert resp.status_code == 401


def test_brand_sync_orders_cross_tenant_denied():
    app.dependency_overrides[get_current_tenant] = _as_tenant_a
    with patch("src.api.routes.brands.supabase_select", side_effect=_brands_table):
        resp = client.post(f"/api/brands/{BRAND_B}/sync-orders")
    assert resp.status_code == 404


# ─── agentic.py ──────────────────────────────────────────────────────────────

def test_get_ticket_analysis_cross_tenant_denied():
    app.dependency_overrides[get_current_tenant] = _as_tenant_a

    def fake_select(table, params=None):
        if table == "tickets":
            return [TICKET_B]
        return _brands_table(table, params)

    with patch("src.lib.supabase_client.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.tickets.supabase_select", side_effect=fake_select):
        resp = client.get(f"/api/agentic/ticket/{TICKET_B['id']}")
    assert resp.status_code == 404
    assert "B issue" not in resp.text


def test_get_ticket_analysis_own_tenant_allowed():
    app.dependency_overrides[get_current_tenant] = _as_tenant_a

    def fake_select(table, params=None):
        if table == "tickets":
            return [TICKET_A]
        return _brands_table(table, params)

    with patch("src.lib.supabase_client.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.tickets.supabase_select", side_effect=fake_select):
        resp = client.get(f"/api/agentic/ticket/{TICKET_A['id']}")
    assert resp.status_code == 200
    assert resp.json()["subject"] == "A issue"


def test_process_ticket_order_lookup_scoped_to_own_brands():
    """order_number is not globally unique across brands — a tenant must
    never see another brand's order total/line items just by supplying its
    order number."""
    app.dependency_overrides[get_current_tenant] = _as_tenant_a

    order_b = {"order_number": 1001, "store_id": BRAND_B, "total_price": "999.00",
               "line_items": [], "created_at": "2026-01-01T00:00:00Z"}

    def fake_select(table, params=None):
        if table == "orders":
            store_filter = params.get("store_id", "")
            if BRAND_B in store_filter:
                return [order_b]
            return []
        return _brands_table(table, params)

    with patch("src.lib.supabase_client.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.tickets.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.agentic.extract_intent_with_llm", new=AsyncMock(return_value={
             "intent": "refund", "requested_item": "", "sentiment_score": 5, "order_id": "1001", "reasoning": "x",
         })):
        resp = client.post("/api/agentic/process-ticket", json={
            "ticket_id": "t1", "customer_email": "attacker@example.com",
            "customer_name": "Attacker", "message_content": "refund order 1001", "order_id": "1001",
        })
    assert resp.status_code == 200
    audit = resp.json()["shopify_audit"]
    # Brand B's order must NOT leak through even though the order_number matched.
    assert audit["order_total"] == 0
    assert audit["items"] == []


# ─── events.py ───────────────────────────────────────────────────────────────

def test_list_events_never_returns_other_tenants_tickets():
    app.dependency_overrides[get_current_tenant] = _as_tenant_a

    def fake_select(table, params=None):
        if table == "tickets":
            store_filter = params.get("brand_id", "")
            return [TICKET_A] if BRAND_A in store_filter else []
        if table == "pending_actions":
            return []
        return _brands_table(table, params)

    with patch("src.lib.supabase_client.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.tickets.supabase_select", side_effect=fake_select):
        resp = client.get("/api/events")
    assert resp.status_code == 200
    body = resp.text
    assert "b@example.com" not in body
    assert "B issue" not in body


def test_list_events_brand_id_param_cannot_probe_other_tenant():
    app.dependency_overrides[get_current_tenant] = _as_tenant_a
    with patch("src.lib.supabase_client.supabase_select") as mock_select, \
         patch("src.api.routes.tickets.supabase_select", side_effect=_brands_table):
        resp = client.get("/api/events", params={"brand_id": BRAND_B})
    assert resp.status_code == 200
    assert resp.json() == []
    mock_select.assert_not_called()


def test_get_event_cross_tenant_denied():
    app.dependency_overrides[get_current_tenant] = _as_tenant_a

    def fake_select(table, params=None):
        if table == "tickets":
            return [TICKET_B]
        return _brands_table(table, params)

    with patch("src.lib.supabase_client.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.tickets.supabase_select", side_effect=fake_select):
        resp = client.get(f"/api/events/evt-{TICKET_B['id']}-email")
    assert resp.status_code == 404
    assert "b@example.com" not in resp.text


def test_get_event_own_tenant_allowed():
    app.dependency_overrides[get_current_tenant] = _as_tenant_a

    def fake_select(table, params=None):
        if table == "tickets":
            return [TICKET_A]
        return _brands_table(table, params)

    with patch("src.lib.supabase_client.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.tickets.supabase_select", side_effect=fake_select):
        resp = client.get(f"/api/events/evt-{TICKET_A['id']}-email")
    assert resp.status_code == 200
    assert resp.json()["customer"]["email"] == "a@example.com"


# ─── legacy returns.py / actions.py routers must not be mounted ────────────

def test_legacy_returns_and_pending_actions_routers_not_registered_in_main():
    main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    with open(main_py, "r", encoding="utf-8") as f:
        src = f.read()
    # The registration calls must be commented out (prefixed with '#'), not live.
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("register_router(returns_router"):
            raise AssertionError("returns_router must not be actively registered")
        if stripped.startswith("register_router(actions_router"):
            raise AssertionError("actions_router (legacy pending_actions) must not be actively registered")


# ─── tickets.py: _get_tenant_brand_ids shopify_domain fallback ─────────────

def test_shopify_domain_fallback_never_returns_another_tenants_brand():
    from src.api.routes.tickets import _get_tenant_brand_ids

    async def fake_get_tenant(tenant_id):
        return {"shopify_domain": "shared-store.myshopify.com"}

    def fake_select(table, params=None):
        if table != "brands":
            return []
        # No brands owned via tenant_id (pre-migration-010 row), but the
        # shopify_domain fallback would match BOTH tenants' brands if the
        # bug were still present.
        if "tenant_id" in params:
            return []
        if "shopify_domain" in params:
            return [
                {"id": BRAND_A, "tenant_id": None, "shopify_domain": "shared-store.myshopify.com"},
                {"id": BRAND_B, "tenant_id": TENANT_B, "shopify_domain": "shared-store.myshopify.com"},
            ]
        return []

    with patch("src.api.routes.tickets.supabase_select", side_effect=fake_select), \
         patch("src.services.auth_service.auth_service.get_tenant", new=AsyncMock(side_effect=fake_get_tenant)):
        import asyncio
        result = asyncio.run(_get_tenant_brand_ids(_as_tenant_a()))

    assert result is not None
    assert BRAND_B not in result  # owned by tenant B — must never leak to tenant A
    assert BRAND_A in result  # unclaimed (tenant_id is None) — safe to self-heal
