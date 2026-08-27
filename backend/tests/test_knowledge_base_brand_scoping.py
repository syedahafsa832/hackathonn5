"""
Merchant Knowledge Base / RAG fix: the only KB UI in the dashboard
(Settings.jsx) wrote through knowledge_base_service.upload_text(), which
inserts rag_chunks with tenant_id only - no brand_id. Since brand_id is
nullable and the live agent's retrieval (match_brand_rag_chunks) filters
strictly on it, anything a merchant uploaded through that UI was stored but
permanently invisible to the AI. The fix repoints Settings.jsx at the
existing, correct, brand-scoped v2 API
(brand_knowledge_service.py / v2_knowledge.py) - no new KB system, no schema
change, no change to the retrieval RPC.

These tests prove the write/read contract this fix depends on:
1. upload_text() stores rag_chunks with the correct brand_id (both the
   manual-upload path and the Shopify-import path, which share this same
   function unmodified).
2. Content uploaded for one brand is retrievable via get_brand_context() for
   that brand.
3. A different brand_id gets nothing back for content scoped elsewhere.
4. get_sources/delete_source stay brand-scoped (service level - complements
   the existing HTTP-level test_knowledge_base_brand_isolation.py, which
   covers upload/delete via TestClient but mocks these methods rather than
   testing their internals).
5. GET /sources and GET /context (list/retrieve) are blocked cross-tenant at
   the HTTP layer too - not just upload/delete.

Plus the rag_engine.py quarantine (see test_rag_engine_no_unscoped_fallback
below): that module has zero live callers (grepped - only referenced in a
comment in customer_success_agent.py), but its get_relevant_context() used
to fall through to an unscoped "match_rag_chunks" RPC (no tenant/brand
filter) whenever a tenant-scoped search came back empty - an ordinary
"no match" case, not just an error. Removed rather than fixed-in-place,
since dormant or not, nothing should ever call an unscoped cross-tenant
search.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.brand_knowledge_service import BrandKnowledgeService  # noqa: E402

BRAND_A = "brand-aaaa"
BRAND_B = "brand-bbbb"
TENANT_A = "tenant-aaaa"


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _service():
    svc = BrandKnowledgeService()
    svc.ai_client = object()  # non-None sentinel — _get_embedding is mocked separately below
    return svc


def _select_brands(table, params=None):
    if table == "brands" and (params or {}).get("id") == f"eq.{BRAND_A}":
        return [{"id": BRAND_A, "tenant_id": TENANT_A}]
    return []


# ── 1. upload_text() tags rag_chunks with the correct brand_id ──────────────

def test_manual_upload_stores_chunk_with_correct_brand_id():
    svc = _service()
    inserted = []

    def fake_insert(table, record):
        if table == "rag_chunks":
            inserted.append(record)
        return record

    with patch("src.services.brand_knowledge_service.supabase_select", side_effect=_select_brands), \
         patch("src.services.brand_knowledge_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.brand_knowledge_service.supabase_update"), \
         patch.object(svc, "_get_embedding", new=AsyncMock(return_value=[0.1, 0.2, 0.3])):
        result = run(svc.upload_text(brand_id=BRAND_A, name="Return Policy", content="Returns accepted within 30 days of purchase for any reason."))

    assert result["success"] is True
    assert len(inserted) >= 1
    assert all(c["brand_id"] == BRAND_A for c in inserted)
    assert all(c["tenant_id"] == TENANT_A for c in inserted)


def test_shopify_import_upload_also_tags_brand_id():
    """Same shared upload_text() the importer calls with source_type='shopify_sync' -
    proves the existing Shopify-import behavior is unaffected by this fix."""
    svc = _service()
    inserted = []

    def fake_insert(table, record):
        if table == "rag_chunks":
            inserted.append(record)
        return record

    with patch("src.services.brand_knowledge_service.supabase_select", side_effect=_select_brands), \
         patch("src.services.brand_knowledge_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.brand_knowledge_service.supabase_update"), \
         patch.object(svc, "_get_embedding", new=AsyncMock(return_value=[0.1, 0.2, 0.3])):
        result = run(svc.upload_text(brand_id=BRAND_A, name="Shipping Policy", content="We ship worldwide within 5-7 business days of order confirmation.", source_type="shopify_sync"))

    assert result["success"] is True
    assert all(c["brand_id"] == BRAND_A for c in inserted)


# ── 2 & 3. Retrieval is correctly brand-scoped both ways ─────────────────────

def test_uploaded_content_retrievable_for_the_correct_brand():
    svc = _service()

    def fake_rpc(fn, params):
        assert fn == "match_brand_rag_chunks"
        if params.get("p_brand_id") == BRAND_A:
            return [{"source_name": "Return Policy", "content": "Returns accepted within 30 days.", "similarity": 0.9}]
        return []

    with patch("src.services.brand_knowledge_service.supabase_rpc", side_effect=fake_rpc), \
         patch.object(svc, "_get_embedding", new=AsyncMock(return_value=[0.1, 0.2, 0.3])):
        context = run(svc.get_brand_context(brand_id=BRAND_A, query="what is your return policy?"))

    assert "Returns accepted within 30 days" in context


def test_content_not_retrievable_for_a_different_brand():
    """The exact cross-tenant-isolation proof: content that matches for
    BRAND_A must return nothing when queried under BRAND_B."""
    svc = _service()

    def fake_rpc(fn, params):
        assert fn == "match_brand_rag_chunks"
        if params.get("p_brand_id") == BRAND_A:
            return [{"source_name": "Return Policy", "content": "Returns accepted within 30 days.", "similarity": 0.9}]
        return []

    with patch("src.services.brand_knowledge_service.supabase_rpc", side_effect=fake_rpc), \
         patch.object(svc, "_get_embedding", new=AsyncMock(return_value=[0.1, 0.2, 0.3])):
        context = run(svc.get_brand_context(brand_id=BRAND_B, query="what is your return policy?"))

    assert context == ""


# ── 4. list/delete stay brand-scoped at the service level ───────────────────

def test_get_sources_filters_by_brand_id():
    svc = _service()
    captured = {}

    def fake_select(table, params=None):
        if table == "knowledge_base_sources":
            captured.update(params or {})
            return [{"id": "src-1", "brand_id": BRAND_A}]
        return []

    with patch("src.services.brand_knowledge_service.supabase_select", side_effect=fake_select):
        sources = run(svc.get_sources(BRAND_A))

    assert captured.get("brand_id") == f"eq.{BRAND_A}"
    assert len(sources) == 1


def test_delete_source_refuses_a_source_owned_by_another_brand():
    svc = _service()

    def fake_select(table, params=None):
        # Simulates PostgREST filtering: source exists but not under BRAND_B
        if table == "knowledge_base_sources" and params.get("brand_id") == f"eq.{BRAND_B}":
            return []
        return []

    with patch("src.services.brand_knowledge_service.supabase_select", side_effect=fake_select), \
         patch("src.services.brand_knowledge_service.supabase_delete") as mock_delete:
        result = run(svc.delete_source(BRAND_B, "some-source-owned-by-brand-a"))

    assert result["success"] is False
    mock_delete.assert_not_called()


# ── 5. HTTP-level: list/retrieve endpoints also block cross-tenant access ───

def test_list_sources_blocked_for_another_tenants_brand():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.routes.v2_knowledge import router as v2_knowledge_router
    from src.api.middleware.tenant_auth import get_current_tenant, TenantContext

    app = FastAPI()
    app.include_router(v2_knowledge_router, prefix="/api/v2")
    test_client = TestClient(app)

    attacker_tenant, attacker_brand, victim_brand = "tenant-attacker", "brand-attacker-owned", "brand-victim"

    def fake_brand_lookup(table, params=None):
        if table == "brands" and params.get("id") == f"eq.{attacker_brand}" and params.get("tenant_id") == f"eq.{attacker_tenant}":
            return [{"id": attacker_brand, "tenant_id": attacker_tenant}]
        return []

    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(
        tenant_id=attacker_tenant, email="attacker@example.com"
    )
    try:
        with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_brand_lookup):
            resp = test_client.get(f"/api/v2/brands/{victim_brand}/knowledge/sources")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404


# ── rag_engine.py: unscoped fallback quarantined ─────────────────────────────

def test_rag_engine_no_unscoped_fallback_without_tenant_id():
    from src.services.rag_engine import RAGEngine
    engine = RAGEngine()
    engine.ai_client = object()
    rpc_calls = []

    def fake_rpc(fn, params):
        rpc_calls.append(fn)
        return []

    with patch("src.services.rag_engine.supabase_rpc", side_effect=fake_rpc), \
         patch.object(engine, "_get_embedding", new=AsyncMock(return_value=[0.1, 0.2])):
        context = run(engine.get_relevant_context("what is your return policy?"))

    assert context == ""
    assert rpc_calls == [], f"expected no RPC calls without a tenant_id, got {rpc_calls}"


def test_rag_engine_never_calls_the_unscoped_match_rag_chunks_rpc():
    """Even when a tenant_id IS given and its own scoped search comes back
    empty (an ordinary no-match, not an error), the old code fell through to
    an unscoped match_rag_chunks call. It must never be reachable now.

    rpc_calls is checked OUTSIDE the `with` block deliberately - an assert
    raised from inside fake_rpc would just be an Exception that
    get_relevant_context's own try/except silently swallows into a normal
    "" return, letting a broken fix pass unnoticed."""
    from src.services.rag_engine import RAGEngine
    engine = RAGEngine()
    engine.ai_client = object()
    rpc_calls = []

    def fake_rpc(fn, params):
        rpc_calls.append(fn)
        return []  # no matches for this tenant - the exact case that used to leak

    with patch("src.services.rag_engine.supabase_rpc", side_effect=fake_rpc), \
         patch.object(engine, "_get_embedding", new=AsyncMock(return_value=[0.1, 0.2])), \
         patch.object(engine, "_tenant_has_kb_docs", return_value=True):
        context = run(engine.get_relevant_context("random query", tenant_id="tenant-x"))

    assert context == ""
    assert rpc_calls == ["match_tenant_rag_chunks"], f"expected only the tenant-scoped RPC, got {rpc_calls}"
