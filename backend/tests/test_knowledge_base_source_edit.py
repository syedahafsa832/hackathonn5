"""
Knowledge Base document viewer/editor — GET .../sources/{id} (reconstructed
content) and PUT .../sources/{id} (edit -> re-index in place).

Covers: viewing a source's actual stored content, editing re-embeds it
under the SAME source_id (never a second/duplicate source), edits are
brand/tenant isolated the same way every other v2_knowledge.py route is,
and a Shopify resync will not silently wipe a document the merchant has
edited (shopify_import_service._clear_previous_import).
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

from src.api.routes import v2_knowledge  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402
from src.services.brand_knowledge_service import brand_knowledge_service  # noqa: E402
import src.services.shopify_import_service as import_mod  # noqa: E402

app = FastAPI()
app.include_router(v2_knowledge.router, prefix="/api/v2")
client = TestClient(app)

TENANT_ID = "tenant-1"
BRAND_ID = "brand-1"
OTHER_TENANT = "tenant-attacker"
SOURCE_ID = "src-1"


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


def _fake_brand_lookup(table, params=None):
    params = params or {}
    if table == "brands" and params.get("id") == f"eq.{BRAND_ID}" and params.get("tenant_id") == f"eq.{TENANT_ID}":
        return [{"id": BRAND_ID, "tenant_id": TENANT_ID}]
    return []


# ══════════════════════════════════════════════════════════════════════════
# GET /sources/{id} — reconstructs readable content from stored chunks
# ══════════════════════════════════════════════════════════════════════════

def test_get_source_reconstructs_content_from_chunks_in_order():
    chunks = [
        {"content": "Second half.", "chunk_index": 1},
        {"content": "First half.", "chunk_index": 0},
    ]

    def fake_select(table, params=None):
        params = params or {}
        if table == "knowledge_base_sources":
            return [{"id": SOURCE_ID, "brand_id": BRAND_ID, "name": "Return Policy", "source_type": "text", "status": "completed"}]
        if table == "rag_chunks":
            # Route asks the DB to order by chunk_index.asc - the fake
            # returns already-sorted rows to model that.
            return sorted(chunks, key=lambda c: c["chunk_index"])
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup), \
         patch("src.api.routes.v2_knowledge.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get(f"/api/v2/brands/{BRAND_ID}/knowledge/sources/{SOURCE_ID}"))

    assert resp.status_code == 200, resp.text
    source = resp.json()["source"]
    assert source["content"] == "First half.\n\nSecond half."
    assert source["actual_chunk_count"] == 2


def test_get_source_cross_tenant_is_blocked():
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup):
        resp = _with_tenant(
            lambda: client.get(f"/api/v2/brands/{BRAND_ID}/knowledge/sources/{SOURCE_ID}"),
            tenant_id=OTHER_TENANT,
        )
    assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════
# PUT /sources/{id} — edit -> re-index in place
# ══════════════════════════════════════════════════════════════════════════

def test_update_source_reuses_the_same_pipeline_and_source_id():
    mock_update = AsyncMock(return_value={"success": True, "source_id": SOURCE_ID, "chunk_count": 3, "total_tokens": 42})
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup), \
         patch.object(v2_knowledge.brand_knowledge_service, "update_source_content", new=mock_update):
        resp = _with_tenant(lambda: client.put(
            f"/api/v2/brands/{BRAND_ID}/knowledge/sources/{SOURCE_ID}",
            json={"content": "Updated return policy text.", "name": "Return Policy (updated)"},
        ))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source_id"] == SOURCE_ID
    assert body["chunk_count"] == 3

    mock_update.assert_awaited_once()
    _, kwargs = mock_update.call_args
    assert kwargs["brand_id"] == BRAND_ID
    assert kwargs["source_id"] == SOURCE_ID
    assert kwargs["content"] == "Updated return policy text."
    assert kwargs["name"] == "Return Policy (updated)"


def test_update_missing_source_returns_404():
    mock_update = AsyncMock(return_value={"success": False, "error": "Source not found"})
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup), \
         patch.object(v2_knowledge.brand_knowledge_service, "update_source_content", new=mock_update):
        resp = _with_tenant(lambda: client.put(
            f"/api/v2/brands/{BRAND_ID}/knowledge/sources/missing-id",
            json={"content": "Some content"},
        ))
    assert resp.status_code == 404


def test_update_source_cross_tenant_is_blocked():
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_brand_lookup):
        resp = _with_tenant(
            lambda: client.put(
                f"/api/v2/brands/{BRAND_ID}/knowledge/sources/{SOURCE_ID}",
                json={"content": "Malicious overwrite attempt."},
            ),
            tenant_id=OTHER_TENANT,
        )
    assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════
# brand_knowledge_service.update_source_content — re-indexes in place,
# marks merchant_edited
# ══════════════════════════════════════════════════════════════════════════

def test_update_source_content_replaces_chunks_and_marks_merchant_edited():
    inserted = []
    deleted_calls = []

    def fake_select(table, params=None):
        params = params or {}
        if table == "knowledge_base_sources" and params.get("id") == f"eq.{SOURCE_ID}":
            return [{"id": SOURCE_ID, "brand_id": BRAND_ID, "tenant_id": TENANT_ID, "name": "Shipping Policy", "metadata": {"type": "shopify_policy"}}]
        return []

    def fake_delete(table, params=None):
        deleted_calls.append((table, params))

    def fake_insert(table, record):
        if table == "rag_chunks":
            inserted.append(record)

    with patch("src.services.brand_knowledge_service.supabase_select", side_effect=fake_select), \
         patch("src.services.brand_knowledge_service.supabase_update") as mock_update, \
         patch("src.services.brand_knowledge_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.brand_knowledge_service.supabase_delete", side_effect=fake_delete), \
         patch.object(brand_knowledge_service, "_get_embedding", new=AsyncMock(return_value=[0.1] * 8)):
        import asyncio
        result = asyncio.run(brand_knowledge_service.update_source_content(
            brand_id=BRAND_ID, source_id=SOURCE_ID, content="We ship worldwide within 5 business days.",
        ))

    assert result["success"] is True
    assert result["chunk_count"] >= 1

    # Old chunks for this source were wiped before the new ones were written.
    assert ("rag_chunks", {"source_id": f"eq.{SOURCE_ID}"}) in deleted_calls
    assert all(c["source_id"] == SOURCE_ID for c in inserted)
    assert all(c["brand_id"] == BRAND_ID and c["tenant_id"] == TENANT_ID for c in inserted)

    # Final status update marks the source completed AND merchant_edited -
    # so a future Shopify resync knows to leave it alone.
    final_call = mock_update.call_args_list[-1]
    updated_fields = final_call.args[2]
    assert updated_fields["status"] == "completed"
    assert updated_fields["metadata"]["merchant_edited"] is True


def test_update_source_content_missing_source_fails_cleanly():
    with patch("src.services.brand_knowledge_service.supabase_select", return_value=[]):
        import asyncio
        result = asyncio.run(brand_knowledge_service.update_source_content(
            brand_id=BRAND_ID, source_id="does-not-exist", content="whatever",
        ))
    assert result["success"] is False
    assert "not found" in result["error"].lower()


# ══════════════════════════════════════════════════════════════════════════
# Shopify resync must not silently wipe a merchant-edited source
# ══════════════════════════════════════════════════════════════════════════

def test_clear_previous_import_preserves_merchant_edited_sources():
    rows = [
        {"id": "s-untouched", "metadata": {"type": "shopify_product_batch"}},
        {"id": "s-edited", "metadata": {"type": "shopify_policy", "merchant_edited": True}},
    ]
    deleted_ids = []

    def fake_select(table, params=None):
        if table == "knowledge_base_sources":
            return rows
        return []

    def fake_delete(table, params=None):
        deleted_ids.append(params.get("id"))

    with patch.object(import_mod, "supabase_select", side_effect=fake_select), \
         patch.object(import_mod, "supabase_delete", side_effect=fake_delete):
        import asyncio
        asyncio.run(import_mod._clear_previous_import(BRAND_ID))

    assert deleted_ids == ["eq.s-untouched"]  # the edited source was left alone
