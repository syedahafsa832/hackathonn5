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
from src.api.routes.brands import router as brands_router  # noqa: E402
from src.api.routes.tickets import router as tickets_router  # noqa: E402


# ─── Minimal app: only the routers under test, real prefixes matching main.py ──

app = FastAPI()
app.include_router(saas_auth_router, prefix="/api/v1")
app.include_router(v2_brands_router, prefix="/api/v2")
app.include_router(v2_tickets_router, prefix="/api/v2")
app.include_router(brands_router, prefix="/api")
app.include_router(tickets_router, prefix="/api")
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

        for key in ("id", "tenant_id", "brand_id", "organization_id", "email", "supabase_user_id", "store_id"):
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

# Stand-in for Supabase Auth: sign_up() hands back a session whose
# access_token is an opaque marker, and verify_jwt() looks up the claims
# that marker was minted with — the same shape a real decoded Supabase JWT
# would have (sub = Supabase user id, email), no "type" claim, so requests
# exercise the real (non-legacy) tenant-resolution path in tenant_auth.py /
# auth_middleware.py, not the deprecated custom-JWT bridge.
_token_claims = {}


def _fake_gotrue_signup(email, password):
    supabase_user_id = f"sb-{uuid.uuid4()}"
    access_token = f"access-{uuid.uuid4()}"
    refresh_token = f"refresh-{uuid.uuid4()}"
    _token_claims[access_token] = {"sub": supabase_user_id, "email": email}
    return {
        "user": {"id": supabase_user_id, "email": email},
        "session": {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer", "expires_in": 3600},
    }


def _verify_jwt_via_stub(token):
    return _token_claims.get(token)


@pytest.fixture(autouse=True)
def _patched_supabase():
    # Security audit finding A3 added real rate limiting to /register and
    # /login (src/lib/rate_limiter.py's shared Limiter). Its storage is
    # process-global, not per-test, and this file registers many tenants in
    # quick succession from the same TestClient "IP" - reset it per test so
    # that's exercising real app behavior, not fighting the limiter.
    from src.lib.rate_limiter import limiter
    limiter.reset()
    with patch("src.services.auth_service.supabase_select", side_effect=db.select), \
         patch("src.services.auth_service.supabase_insert", side_effect=db.insert), \
         patch("src.services.auth_service.supabase_update", side_effect=db.update), \
         patch("src.services.auth_service.supabase_gotrue.sign_up", side_effect=_fake_gotrue_signup), \
         patch("src.services.supabase_auth_service.supabase_select", side_effect=db.select), \
         patch("src.api.routes.v2_brands.supabase_select", side_effect=db.select), \
         patch("src.api.routes.v2_brands.supabase_update", side_effect=db.update), \
         patch("src.api.routes.v2_tickets.supabase_select", side_effect=db.select), \
         patch("src.api.routes.v2_tickets.supabase_update", side_effect=db.update), \
         patch("src.api.routes.brands.supabase_select", side_effect=db.select), \
         patch("src.api.routes.tickets.supabase_select", side_effect=db.select), \
         patch("src.api.routes.tickets.supabase_update", side_effect=db.update), \
         patch("src.services.supabase_service.supabase_select", side_effect=db.select), \
         patch("src.services.supabase_service.supabase_update", side_effect=db.update), \
         patch("src.services.brand_manager.supabase_select", side_effect=db.select), \
         patch("src.services.brand_manager.supabase_update", side_effect=db.update), \
         patch("src.api.middleware.auth_middleware.supabase_auth_service.verify_jwt",
               side_effect=_verify_jwt_via_stub):
        yield
    db.tenants.clear()
    db.brands.clear()
    db.tickets.clear()
    _token_claims.clear()
    from src.services.brand_manager import brand_manager as _bm
    _bm._brand_cache.clear()
    from src.api.routes.tickets import _invalidate_tickets_cache
    _invalidate_tickets_cache()


def _register(email: str) -> dict:
    resp = client.post("/api/v1/auth/register", json={
        "email": email, "password": "supersecret123", "company_name": email.split("@")[0].title(),
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def zero_brand_tenant():
    """A tenant with ZERO owned brands — the exact reachable state
    (documented in _create_default_brand's own docstring: default-brand
    creation can fail, or a brand can later be unlinked/removed) that used
    to make _get_tenant_brand_ids() return None instead of []. Registration
    auto-creates one default brand, so this strips it back out to reproduce
    the zero-brand case deterministically."""
    org_c = _register("org-c@example.com")
    for bid in [b["id"] for b in db.brands.values() if b["tenant_id"] == org_c["tenant_id"]]:
        del db.brands[bid]
    return org_c


# ─── Setup: two real tenants, each with a ticket on their auto-created brand ─

@pytest.fixture
def two_tenants():
    org_a = _register("org-a@example.com")
    org_b = _register("org-b@example.com")

    brand_a = next(iter(db.brands.values()))
    brand_b = next(b for b in db.brands.values() if b["tenant_id"] == org_b["tenant_id"])

    # store_id is the tickets table's real brand FK (brand_id is a secondary
    # alias present on some rows — see tickets.py's get_ticket comment); set
    # both so fixture data matches production shape for every router under test.
    ticket_a = db.insert("tickets", {"brand_id": brand_a["id"], "store_id": brand_a["id"], "subject": "A's issue", "status": "open", "customer_email": "cust@a.com"})
    ticket_b = db.insert("tickets", {"brand_id": brand_b["id"], "store_id": brand_b["id"], "subject": "B's issue", "status": "open", "customer_email": "cust@b.com"})

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


# ─── 6-9. The plain (non-v2) brands.py router: GET/PUT/DELETE /api/brands/{id}
# had NO auth dependency at all before this fix — any caller, authenticated
# or not, who knew/guessed a brand UUID could read another tenant's
# support_email/email_signature, overwrite their shopify_access_token, or
# deactivate their brand. This is the router Brands.jsx's "Save" button
# actually calls (client.put('/api/brands/${id}', ...)), so it's live,
# in-use code, not a dead legacy path. ────────────────────────────────────

def test_6_get_plain_brand_with_no_auth_header_is_rejected(two_tenants):
    """The original bug: zero Authorization header at all still worked."""
    resp = client.get(f"/api/brands/{two_tenants['brand_a']['id']}")
    assert resp.status_code in (401, 403)


def test_7_get_plain_brand_cross_tenant_returns_404_not_data(two_tenants):
    resp = client.get(
        f"/api/brands/{two_tenants['brand_b']['id']}",
        headers=_auth(two_tenants["org_a"]["access_token"]),
    )
    assert resp.status_code == 404
    assert two_tenants["brand_b"]["name"] not in resp.text


def test_8_put_plain_brand_cross_tenant_returns_404_and_does_not_modify(two_tenants):
    """Before the fix, this would have silently renamed org B's brand and
    could have overwritten their shopify_access_token."""
    resp = client.put(
        f"/api/brands/{two_tenants['brand_b']['id']}",
        json={"name": "HIJACKED", "shopify_access_token": "attacker-controlled-token"},
        headers=_auth(two_tenants["org_a"]["access_token"]),
    )
    assert resp.status_code == 404
    assert db.brands[two_tenants["brand_b"]["id"]]["name"] != "HIJACKED"
    assert db.brands[two_tenants["brand_b"]["id"]].get("shopify_access_token") != "attacker-controlled-token"


def test_9_delete_plain_brand_cross_tenant_returns_404_and_does_not_deactivate(two_tenants):
    resp = client.delete(
        f"/api/brands/{two_tenants['brand_b']['id']}",
        headers=_auth(two_tenants["org_a"]["access_token"]),
    )
    assert resp.status_code == 404
    assert db.brands[two_tenants["brand_b"]["id"]]["is_active"] is True


def test_10_own_org_can_still_read_and_update_its_own_brand_via_plain_router(two_tenants):
    """Proves the 404s above are real isolation, not a broken endpoint."""
    get_resp = client.get(
        f"/api/brands/{two_tenants['brand_a']['id']}",
        headers=_auth(two_tenants["org_a"]["access_token"]),
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == two_tenants["brand_a"]["id"]

    put_resp = client.put(
        f"/api/brands/{two_tenants['brand_a']['id']}",
        json={"email_signature": "— The A Team"},
        headers=_auth(two_tenants["org_a"]["access_token"]),
    )
    assert put_resp.status_code == 200
    assert db.brands[two_tenants["brand_a"]["id"]]["email_signature"] == "— The A Team"


# ─── 11-15. tickets.py's own router (/api/tickets) — regression coverage for
# the _get_tenant_brand_ids() None/[] finding. This helper used to return
# None when a tenant owned zero brands, and several call sites here checked
# `if brand_ids is not None and X not in brand_ids`, which skipped the
# ownership check entirely for a None return - letting a zero-brand tenant
# pass through ANY other tenant's store_id/ticket_id untouched. Fixed by
# always returning a real list ([] for "owns nothing") and comparing against
# it unconditionally at every call site. ─────────────────────────────────

def test_11_tickets_list_by_store_id_cross_tenant_returns_empty_not_data(two_tenants):
    """GET /api/tickets?store_id=<other tenant's brand> must not return their tickets."""
    resp = client.get(
        f"/api/tickets?store_id={two_tenants['brand_b']['id']}",
        headers=_auth(two_tenants["org_a"]["access_token"]),
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_12_tickets_get_single_cross_tenant_returns_404_not_data(two_tenants):
    resp = client.get(
        f"/api/tickets/{two_tenants['ticket_b']['id']}",
        headers=_auth(two_tenants["org_a"]["access_token"]),
    )
    assert resp.status_code == 404
    assert "B's issue" not in resp.text


def test_13_zero_brand_tenant_cannot_list_another_tenants_store(two_tenants, zero_brand_tenant):
    """The core regression: a tenant that owns zero brands must not be able
    to pass another tenant's store_id through and read their tickets."""
    resp = client.get(
        f"/api/tickets?store_id={two_tenants['brand_a']['id']}",
        headers=_auth(zero_brand_tenant["access_token"]),
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_14_zero_brand_tenant_cannot_get_another_tenants_ticket(two_tenants, zero_brand_tenant):
    """Same regression, single-ticket-fetch path (GET /api/tickets/{id})."""
    resp = client.get(
        f"/api/tickets/{two_tenants['ticket_a']['id']}",
        headers=_auth(zero_brand_tenant["access_token"]),
    )
    assert resp.status_code == 404
    assert "A's issue" not in resp.text


def test_15_own_org_can_still_list_and_get_own_tickets_via_tickets_router(two_tenants):
    """Positive control: the fix must not break a tenant's access to its own
    data — proves the 404/empty results above are real isolation, not a
    broken endpoint that denies everyone."""
    list_resp = client.get(
        "/api/tickets",
        headers=_auth(two_tenants["org_a"]["access_token"]),
    )
    assert list_resp.status_code == 200
    subjects = [t["subject"] for t in list_resp.json()]
    assert "A's issue" in subjects
    assert "B's issue" not in subjects

    get_resp = client.get(
        f"/api/tickets/{two_tenants['ticket_a']['id']}",
        headers=_auth(two_tenants["org_a"]["access_token"]),
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["subject"] == "A's issue"
