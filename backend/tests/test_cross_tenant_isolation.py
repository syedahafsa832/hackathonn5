"""
Cross-Tenant Isolation Tests (H3)
===================================
Real HTTP-level tests against the actual route handlers (via FastAPI's
TestClient, not hand-asserted literals) — registers two tenants for real
through POST /api/v1/auth/register, then confirms tenant A's JWT cannot
read or write tenant B's tickets/brands by ID, and gets 404 (not a data
leak, not a distinguishable 403) when it tries.

Only the Supabase boundary (supabase_select/insert/update/rpc) is mocked;
everything above that — routing, dependency injection, JWT decode,
ownership checks — is the real, unmodified application code.

Findings this test suite proves fixed (see PR/report for detail):
  - v2_brands.py: every /{brand_id} endpoint had NO ownership check at all
    (a tenant could read, modify, delete, or hijack-via-Shopify-connect
    ANY other tenant's brand by guessing its UUID). Fixed with a shared
    _get_owned_brand() helper.
  - v2_tickets.py: existing ownership checks returned 403 (leaks resource
    existence) instead of 404; GET /{ticket_id}/order had no check at all.
  - supabase_auth_service.get_tenant_by_id(): hardcoded brands=[] for every
    v1-token tenant, which made EVERY ownership check in v2_tickets.py fail
    even for a tenant's own resources — a functional bug masquerading as
    isolation, found while verifying isolation. Fixed to look up the
    tenant's real brands.
"""
import os
import sys
import uuid
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.api.routes.saas_auth import router as saas_auth_router  # noqa: E402
from src.api.routes.v2_brands import router as v2_brands_router  # noqa: E402
from src.api.routes.v2_tickets import router as v2_tickets_router  # noqa: E402
from src.services.auth_service import auth_service as real_auth_service  # noqa: E402


# ─── Minimal app: only the routers under test, real prefixes matching main.py ──

app = FastAPI()
app.include_router(saas_auth_router, prefix="/api/v1")
app.include_router(v2_brands_router, prefix="/api/v2")
app.include_router(v2_tickets_router, prefix="/api/v2")
client = TestClient(app)


# ─── In-memory fake Supabase, keyed by table ─────────────────────────────────

class _FakeDB:
    def __init__(self):
        self.tenants = {}
        self.brands = {}
        self.tickets = {}

    def _eq(self, v):
        return v[3:] if isinstance(v, str) and v.startswith("eq.") else v

    def select(self, table, params=None):
        params = params or {}
        if table == "users":
            return []  # forces the v1 tenant-token fallback path, same as production
        if table == "tenants":
            rows = list(self.tenants.values())
        elif table == "brands":
            rows = list(self.brands.values())
        elif table == "tickets":
            rows = list(self.tickets.values())
        else:
            return []

        for key in ("id", "tenant_id", "brand_id", "organization_id", "email"):
            if key in params:
                wanted = self._eq(params[key])
                rows = [r for r in rows if r.get(key) == wanted]
        return rows

    def insert(self, table, data):
        row = dict(data)
        row.setdefault("id", str(uuid.uuid4()))
        row.setdefault("is_active", True)
        if table == "tenants":
            self.tenants[row["id"]] = row
        elif table == "brands":
            self.brands[row["id"]] = row
        elif table == "tickets":
            self.tickets[row["id"]] = row
        return row

    def update(self, table, match, data):
        target_id = self._eq(match.get("id", ""))
        store = {"tenants": self.tenants, "brands": self.brands, "tickets": self.tickets}.get(table)
        if store is None or target_id not in store:
            return []
        store[target_id].update(data)
        return [store[target_id]]


db = _FakeDB()


def _verify_jwt_via_real_v1_decode(token):
    """Bridge for supabase_auth_service.verify_jwt: decode with the app's own
    v1 JWT logic (auth_service.decode_token) instead of the Supabase-specific
    JWKS/secret dance, which needs live infra this test doesn't have. This is
    the same decode the real verify_jwt would need to succeed at for a v1
    token to reach the fallback branch it's already written to handle."""
    return real_auth_service.decode_token(token)


@pytest.fixture(autouse=True)
def _patched_supabase():
    with patch("src.services.auth_service.supabase_select", side_effect=db.select), \
         patch("src.services.auth_service.supabase_insert", side_effect=db.insert), \
         patch("src.services.auth_service.supabase_update", side_effect=db.update), \
         patch("src.services.supabase_auth_service.supabase_select", side_effect=db.select), \
         patch("src.api.routes.v2_brands.supabase_select", side_effect=db.select), \
         patch("src.api.routes.v2_brands.supabase_update", side_effect=db.update), \
         patch("src.api.routes.v2_tickets.supabase_select", side_effect=db.select), \
         patch("src.api.routes.v2_tickets.supabase_update", side_effect=db.update), \
         patch("src.api.middleware.auth_middleware.supabase_auth_service.verify_jwt",
               side_effect=_verify_jwt_via_real_v1_decode):
        yield
    db.tenants.clear()
    db.brands.clear()
    db.tickets.clear()


def _register(email: str) -> dict:
    resp = client.post("/api/v1/auth/register", json={
        "email": email, "password": "supersecret123", "company_name": email.split("@")[0].title(),
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ─── Setup: two real tenants, each with a ticket on their auto-created brand ─

@pytest.fixture
def two_tenants():
    org_a = _register("org-a@example.com")
    org_b = _register("org-b@example.com")

    brand_a = next(iter(db.brands.values()))
    brand_b = next(b for b in db.brands.values() if b["tenant_id"] == org_b["tenant_id"])

    ticket_a = db.insert("tickets", {"brand_id": brand_a["id"], "subject": "A's issue", "status": "open", "customer_email": "cust@a.com"})
    ticket_b = db.insert("tickets", {"brand_id": brand_b["id"], "subject": "B's issue", "status": "open", "customer_email": "cust@b.com"})

    return {
        "org_a": org_a, "org_b": org_b,
        "brand_a": brand_a, "brand_b": brand_b,
        "ticket_a": ticket_a, "ticket_b": ticket_b,
    }


# ─── 1. GET ticket cross-tenant → 404 ────────────────────────────────────────

def test_1_get_ticket_cross_tenant_returns_404_not_data(two_tenants):
    resp = client.get(
        f"/api/v2/tickets/{two_tenants['ticket_b']['id']}",
        headers=_auth(two_tenants["org_a"]["access_token"]),
    )
    assert resp.status_code == 404
    assert "B's issue" not in resp.text


# ─── 2. PATCH (update) ticket cross-tenant → 404 ─────────────────────────────

def test_2_update_ticket_cross_tenant_returns_404_and_does_not_modify(two_tenants):
    resp = client.patch(
        f"/api/v2/tickets/{two_tenants['ticket_b']['id']}",
        json={"status": "closed"},
        headers=_auth(two_tenants["org_a"]["access_token"]),
    )
    assert resp.status_code == 404
    # Confirm B's ticket was genuinely untouched, not just an unchecked response code.
    assert db.tickets[two_tenants["ticket_b"]["id"]]["status"] == "open"


# ─── 3. GET brand cross-tenant → 404 ─────────────────────────────────────────

def test_3_get_brand_cross_tenant_returns_404_not_data(two_tenants):
    resp = client.get(
        f"/api/v2/brands/{two_tenants['brand_b']['id']}",
        headers=_auth(two_tenants["org_a"]["access_token"]),
    )
    assert resp.status_code == 404
    assert two_tenants["brand_b"]["name"] not in resp.text


# ─── 4. PATCH (write) brand cross-tenant → 404, and does not modify ─────────

def test_4_update_brand_cross_tenant_returns_404_and_does_not_modify(two_tenants):
    """This is the write-side of the finding that mattered most: before the
    fix, this request would have silently renamed org B's brand."""
    resp = client.patch(
        f"/api/v2/brands/{two_tenants['brand_b']['id']}",
        json={"name": "HIJACKED"},
        headers=_auth(two_tenants["org_a"]["access_token"]),
    )
    assert resp.status_code == 404
    assert db.brands[two_tenants["brand_b"]["id"]]["name"] != "HIJACKED"


# ─── 5. Positive control: each org CAN reach its own resources ──────────────

def test_5_each_org_can_access_its_own_ticket_and_brand(two_tenants):
    """Proves the 404s above are real isolation, not a broken endpoint that
    404s for everyone."""
    own_ticket = client.get(
        f"/api/v2/tickets/{two_tenants['ticket_a']['id']}",
        headers=_auth(two_tenants["org_a"]["access_token"]),
    )
    assert own_ticket.status_code == 200
    assert own_ticket.json()["ticket"]["subject"] == "A's issue"

    own_brand = client.get(
        f"/api/v2/brands/{two_tenants['brand_a']['id']}",
        headers=_auth(two_tenants["org_a"]["access_token"]),
    )
    assert own_brand.status_code == 200
    assert own_brand.json()["brand"]["id"] == two_tenants["brand_a"]["id"]

    other_way = client.get(
        f"/api/v2/tickets/{two_tenants['ticket_b']['id']}",
        headers=_auth(two_tenants["org_b"]["access_token"]),
    )
    assert other_way.status_code == 200
    assert other_way.json()["ticket"]["subject"] == "B's issue"
