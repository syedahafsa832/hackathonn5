"""
Cross-tenant IDOR in Knowledge Base upload/delete (security audit finding A2),
re-verified after v2_knowledge.py's auth was switched from the stale
org-based auth_middleware (require_admin/require_brand_access, which check
brands.organization_id - a column the current tenant-per-brand model doesn't
populate, which was also why "Failed to load knowledge base sources" was
showing for every real merchant) to the same tenant_auth.get_current_tenant +
_get_owned_brand ownership check every other v2 brand-scoped endpoint in
v2_brands.py already uses.

Every route in v2_knowledge.py now calls _get_owned_brand(brand_id,
tenant.tenant_id) before doing anything else - a 404 (not 403) for a brand
owned by a different tenant, same pattern/response as v2_brands.py.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes.v2_knowledge import router as v2_knowledge_router  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402

app = FastAPI()
app.include_router(v2_knowledge_router, prefix="/api/v2")
client = TestClient(app)

ATTACKER_TENANT = "tenant-attacker"
ATTACKER_OWN_BRAND = "brand-attacker-owned"
VICTIM_BRAND = "brand-victim"


def _fake_brand_lookup(table, params=None):
    """_get_owned_brand queries brands filtered by id + tenant_id - only
    ATTACKER_OWN_BRAND resolves under ATTACKER_TENANT."""
    if table == "brands" and params.get("id") == f"eq.{ATTACKER_OWN_BRAND}" \
       and params.get("tenant_id") == f"eq.{ATTACKER_TENANT}":
        return [{"id": ATTACKER_OWN_BRAND, "tenant_id": ATTACKER_TENANT}]
    return []


def setup_function():
    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(
        tenant_id=ATTACKER_TENANT, email="attacker@example.com"
    )


def teardown_function():
    app.dependency_overrides.clear()


def test_upload_to_another_tenants_brand_is_blocked():
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup):
        resp = client.post(
            f"/api/v2/brands/{VICTIM_BRAND}/knowledge/upload",
            json={"name": "malicious", "content": "x" * 20},
        )
    assert resp.status_code == 404


def test_delete_another_tenants_brand_source_is_blocked():
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup):
        resp = client.delete(f"/api/v2/brands/{VICTIM_BRAND}/knowledge/sources/some-source-id")
    assert resp.status_code == 404


def test_upload_to_own_brand_still_works():
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup), \
         patch("src.api.routes.v2_knowledge.brand_knowledge_service.upload_text", new=AsyncMock(return_value={
             "success": True, "source_id": "src-1", "chunk_count": 1, "total_tokens": 10,
         })):
        resp = client.post(
            f"/api/v2/brands/{ATTACKER_OWN_BRAND}/knowledge/upload",
            json={"name": "legit", "content": "x" * 20},
        )
    assert resp.status_code == 200


def test_delete_own_brand_source_still_works():
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup), \
         patch("src.api.routes.v2_knowledge.brand_knowledge_service.delete_source", new=AsyncMock(return_value={"success": True})):
        resp = client.delete(f"/api/v2/brands/{ATTACKER_OWN_BRAND}/knowledge/sources/some-source-id")
    assert resp.status_code == 200
