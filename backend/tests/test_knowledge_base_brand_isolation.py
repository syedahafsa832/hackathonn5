"""
Cross-tenant IDOR in Knowledge Base upload/delete (security audit finding A2).

v2_knowledge.py's /upload and DELETE /sources/{source_id} used
Depends(require_admin) - which only checks the caller's own role, never that
brand_id in the URL belongs to them - while every sibling endpoint in the
same file (GET /sources, /search, /context, /stats) already used
Depends(require_brand_access("brand_id")). Any admin-role user could write to
or delete another tenant's knowledge base, and since KB content is injected
verbatim into that brand's live AI system prompt, this was also a path to
poisoning another merchant's real customer-facing AI responses.

Fix: both endpoints now ALSO require require_brand_access("brand_id"),
alongside the existing require_admin (so the "must be an admin" requirement
is preserved, not weakened - this only adds the missing ownership check).

Real HTTP-level test via FastAPI's TestClient and the actual (unmodified)
require_admin/require_brand_access dependency chain - only get_current_user
is overridden (standard FastAPI testing technique) so the test doesn't need
to simulate real Supabase Auth JWTs; supabase_select/brand_knowledge_service
are mocked at the boundary.
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
from src.api.middleware.auth_middleware import get_current_user, UserContext, AuthenticatedContext, UserRole  # noqa: E402

app = FastAPI()
app.include_router(v2_knowledge_router, prefix="/api/v2")
client = TestClient(app)

ATTACKER_ORG = "org-attacker"
ATTACKER_OWN_BRAND = "brand-attacker-owned"
VICTIM_BRAND = "brand-victim"


def _attacker_context() -> AuthenticatedContext:
    """An admin-role user, but only within their own org/brand."""
    return AuthenticatedContext(
        user=UserContext(
            user_id="user-attacker", supabase_auth_id="auth-attacker",
            organization_id=ATTACKER_ORG, email="attacker@example.com",
            role=UserRole.ADMIN, brands=[ATTACKER_OWN_BRAND],
        ),
        organization=None,
        brand_ids=[ATTACKER_OWN_BRAND],
    )


def _fake_brand_lookup(table, params=None):
    """require_brand_access's admin branch re-verifies the brand belongs to
    the caller's own organization_id via a live supabase_select - simulate
    that: only ATTACKER_OWN_BRAND resolves under ATTACKER_ORG."""
    if table == "brands" and params.get("id") == f"eq.{ATTACKER_OWN_BRAND}" \
       and params.get("organization_id") == f"eq.{ATTACKER_ORG}":
        return [{"id": ATTACKER_OWN_BRAND, "organization_id": ATTACKER_ORG}]
    return []


def setup_function():
    app.dependency_overrides[get_current_user] = _attacker_context


def teardown_function():
    app.dependency_overrides.clear()


def test_upload_to_another_tenants_brand_is_blocked():
    with patch("src.lib.supabase_client.supabase_select", side_effect=_fake_brand_lookup):
        resp = client.post(
            f"/api/v2/brands/{VICTIM_BRAND}/knowledge/upload",
            json={"name": "malicious", "content": "x" * 20},
        )
    assert resp.status_code in (403, 404)


def test_delete_another_tenants_brand_source_is_blocked():
    with patch("src.lib.supabase_client.supabase_select", side_effect=_fake_brand_lookup):
        resp = client.delete(f"/api/v2/brands/{VICTIM_BRAND}/knowledge/sources/some-source-id")
    assert resp.status_code in (403, 404)


def test_upload_to_own_brand_still_works():
    with patch("src.lib.supabase_client.supabase_select", side_effect=_fake_brand_lookup), \
         patch("src.api.routes.v2_knowledge.brand_knowledge_service.upload_text", new=AsyncMock(return_value={
             "success": True, "source_id": "src-1", "chunk_count": 1, "total_tokens": 10,
         })):
        resp = client.post(
            f"/api/v2/brands/{ATTACKER_OWN_BRAND}/knowledge/upload",
            json={"name": "legit", "content": "x" * 20},
        )
    assert resp.status_code == 200


def test_delete_own_brand_source_still_works():
    with patch("src.lib.supabase_client.supabase_select", side_effect=_fake_brand_lookup), \
         patch("src.api.routes.v2_knowledge.brand_knowledge_service.delete_source", new=AsyncMock(return_value={"success": True})):
        resp = client.delete(f"/api/v2/brands/{ATTACKER_OWN_BRAND}/knowledge/sources/some-source-id")
    assert resp.status_code == 200
