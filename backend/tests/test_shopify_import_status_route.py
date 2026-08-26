"""
Shopify import start/status routes (v2_brands.py) — the "don't re-import
every onboarding visit" fix.

shopify_import_service._import_status is an in-memory, single-process
dict that resets to "not_started" on every restart/redeploy, even though
the real knowledge (knowledge_base_sources rows) is still in the database.
Before this fix, POST /shopify/import trusted only that in-memory flag,
so re-mounting the onboarding "Import your store" step after a restart
would silently wipe and re-fetch/re-embed the merchant's entire catalog
for no reason. These tests prove knowledge_base_sources.status is now the
real source of truth for "already imported", both for starting a new
import and for reporting status.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import v2_brands  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402
import src.services.shopify_import_service as import_mod  # noqa: E402
import src.services.shopify_scope_service as scope_mod  # noqa: E402

app = FastAPI()
app.include_router(v2_brands.router, prefix="/api/v2")
client = TestClient(app)

BRAND_ID = "brand-1"
TENANT_ID = "tenant-1"
BRAND = {
    "id": BRAND_ID, "tenant_id": TENANT_ID, "name": "Test Brand",
    "shopify_connected": True, "shopify_domain": "test.myshopify.com",
    "shopify_access_token": "encrypted", "shopify_granted_scopes": ["read_products", "read_content"],
}


@pytest.fixture(autouse=True)
def _reset_state():
    import_mod._import_status.pop(BRAND_ID, None)
    import_mod._import_missing_scopes.pop(BRAND_ID, None)
    import_mod._import_report.pop(BRAND_ID, None)
    scope_mod._blocked_imports.clear()
    yield
    import_mod._import_status.pop(BRAND_ID, None)
    import_mod._import_missing_scopes.pop(BRAND_ID, None)
    import_mod._import_report.pop(BRAND_ID, None)
    scope_mod._blocked_imports.clear()


def _override_tenant(tenant_id=TENANT_ID):
    async def _dep():
        return TenantContext(tenant_id=tenant_id, email="merchant@example.com")
    return _dep


def _with_tenant(fn, tenant_id=TENANT_ID):
    app.dependency_overrides[get_current_tenant] = _override_tenant(tenant_id)
    try:
        return fn()
    finally:
        app.dependency_overrides.clear()


def _fake_select(brand=BRAND, kb_sources=None):
    def fn(table, params=None):
        params = params or {}
        if table == "brands":
            wanted_tenant = params.get("tenant_id", "").replace("eq.", "")
            if wanted_tenant and wanted_tenant != brand.get("tenant_id"):
                return []
            return [brand]
        if table == "knowledge_base_sources":
            rows = kb_sources if kb_sources is not None else []
            if params.get("status") == "eq.completed":
                rows = [r for r in rows if r.get("status") == "completed"]
            return rows
        return []
    return fn


def test_starting_import_creates_a_background_task_for_a_fresh_brand():
    # Never monkeypatch asyncio.create_task itself - `import asyncio` in
    # v2_brands.py binds to the real global module, so replacing its
    # create_task would break TestClient's/pytest-asyncio's own async
    # machinery for the whole process. Patching run_shopify_import (what
    # actually gets scheduled) is enough: AsyncMock() records the call the
    # instant it's invoked to build the coroutine, before create_task ever
    # touches it, and the mocked coroutine is trivially fast/harmless for
    # the real create_task to run for real.
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_select(kb_sources=[])), \
         patch.object(import_mod, "run_shopify_import", new=AsyncMock()) as mock_run, \
         patch("src.services.supabase_service.supabase_service.log_onboarding_event", new=AsyncMock()):
        resp = _with_tenant(lambda: client.post(f"/api/v2/brands/{BRAND_ID}/shopify/import"))

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "running"
    mock_run.assert_called_once_with(BRAND_ID)


def test_repeated_import_calls_do_not_start_duplicate_imports_after_a_restart():
    """Simulates the real bug: knowledge already fully imported (completed
    knowledge_base_sources rows exist), but the in-memory _import_status
    dict is at its post-restart default ("not_started") - as it would be
    on a fresh server process. A second /shopify/import call (e.g. the
    onboarding page reloading) must NOT re-trigger the import."""
    completed_sources = [{"id": "s1", "status": "completed", "brand_id": BRAND_ID}]
    assert import_mod.get_import_status(BRAND_ID) == "not_started"  # the restart condition

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_select(kb_sources=completed_sources)), \
         patch.object(import_mod, "run_shopify_import", new=AsyncMock()) as mock_run:
        resp = _with_tenant(lambda: client.post(f"/api/v2/brands/{BRAND_ID}/shopify/import"))

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "done"
    mock_run.assert_not_called()  # never scheduled a re-import


def test_import_status_reports_done_from_db_even_when_in_memory_status_was_reset():
    """The same restart scenario, from the polling endpoint's perspective."""
    completed_sources = [{"id": "s1", "name": "Products (batch 1)", "status": "completed", "chunk_count": 5, "metadata": {}}]
    assert import_mod.get_import_status(BRAND_ID) == "not_started"

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_select(kb_sources=completed_sources)):
        resp = _with_tenant(lambda: client.get(f"/api/v2/brands/{BRAND_ID}/shopify/import-status"))

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "done"
    assert data["ready"] is True


def test_import_status_ready_false_while_genuinely_running():
    import_mod._import_status[BRAND_ID] = "running"
    completed_sources = [{"id": "s1", "name": "Products (batch 1)", "status": "completed", "chunk_count": 5, "metadata": {}}]

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_select(kb_sources=completed_sources)):
        resp = _with_tenant(lambda: client.get(f"/api/v2/brands/{BRAND_ID}/shopify/import-status"))

    data = resp.json()
    assert data["status"] == "running"
    assert data["ready"] is False  # a re-import is in flight - not safe to treat as ready yet


def test_import_status_not_ready_when_nothing_imported_yet():
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_select(kb_sources=[])):
        resp = _with_tenant(lambda: client.get(f"/api/v2/brands/{BRAND_ID}/shopify/import-status"))

    data = resp.json()
    assert data["status"] == "not_started"
    assert data["ready"] is False


def test_import_status_reports_failed_when_no_completed_sources_exist():
    import_mod._import_status[BRAND_ID] = "failed"

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_select(kb_sources=[])):
        resp = _with_tenant(lambda: client.get(f"/api/v2/brands/{BRAND_ID}/shopify/import-status"))

    data = resp.json()
    assert data["status"] == "failed"
    assert data["ready"] is False


def test_import_status_isolated_by_tenant():
    """A merchant must never see another brand's import status."""
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_select(brand=BRAND)):
        resp = _with_tenant(
            lambda: client.get(f"/api/v2/brands/{BRAND_ID}/shopify/import-status"),
            tenant_id="tenant-OTHER",
        )
    assert resp.status_code == 404
