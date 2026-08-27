"""
Regression coverage for the "Test Luna doesn't know our products" bug.

Root cause (confirmed against the live DB for the affected brand): product
data WAS correctly imported and brand-scoped (5 real product chunks, tagged
brand_id=<this brand>), and RAG retrieval WAS correctly brand-scoped. The
bug was purely a ranking/crowding-out problem: this brand's Privacy Policy
import produced 24 chunks of dense, generic legal boilerplate that outrank
the 5 product chunks in cosine similarity for almost any short/generic
question (empirically: even top_k=40 - the entire corpus - still put actual
product chunks at ranks 9, 18, 23, 30, 31). With get_brand_context's real
top_k=5, the agent's KNOWLEDGE BASE context was made up entirely of privacy
policy text, so it truthfully had no product data to answer from.

Fix: get_brand_context() over-fetches a wider RPC candidate pool, then
drops chunks tagged metadata.type == "shopify_policy" whose title matches
a small privacy/terms-of-service denylist, before truncating to top_k. The
rows themselves are untouched - brand scoping (p_brand_id in the RPC call)
is completely unchanged.

Also covers the "Test Luna" false-positive: v2_brands.py's /test-reply now
forwards the agent's own `escalate` signal, so the onboarding UI can tell
"Luna answered, but wasn't confident she had grounded knowledge" apart from
a real pass instead of treating any non-erroring HTTP response as a pass.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.brand_knowledge_service import (  # noqa: E402
    BrandKnowledgeService,
    _is_low_value_policy_chunk,
)

BRAND_A = "brand-aaaa"
BRAND_B = "brand-bbbb"


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _service():
    svc = BrandKnowledgeService()
    svc.ai_client = object()
    return svc


def _privacy_chunk(i):
    return {
        "source_name": "Privacy policy",
        "content": f"We collect personal information such as device data (chunk {i}) as described in this policy.",
        "similarity": 0.75 - i * 0.001,  # ranks above every product chunk, matching the live bug
        "metadata": {"type": "shopify_policy", "policy_title": "Privacy policy"},
    }


def _product_chunk(name, similarity):
    return {
        "source_name": "Products (batch 1)",
        "content": f"Product: {name}\nDescription: A real item this store actually sells.\nURL: https://example.com/products/{name.lower()}",
        "similarity": similarity,
        "metadata": {"type": "shopify_product_batch", "count": 5},
    }


def _real_policy_chunk():
    """A genuinely useful policy (shipping/returns) must NOT be filtered -
    only the specific privacy/terms-of-service denylist is low-value."""
    return {
        "source_name": "Refund policy",
        "content": "Returns accepted within 30 days of purchase for a full refund.",
        "similarity": 0.80,
        "metadata": {"type": "shopify_policy", "policy_title": "Refund policy"},
    }


def _mixed_corpus_rpc(p_brand_id, brand_products, match_count):
    """Simulates the live corpus shape: 24 privacy-policy chunks outranking
    a handful of product chunks, ORDER BY similarity DESC, LIMIT match_count -
    exactly what match_brand_rag_chunks does in Postgres."""
    if p_brand_id != BRAND_A:
        return []
    rows = [_privacy_chunk(i) for i in range(24)] + brand_products + [_real_policy_chunk()]
    rows.sort(key=lambda r: r["similarity"], reverse=True)
    return rows[:match_count]


# ── 1 & 3. Current brand's products are retrieved and form a useful answer ──

def test_product_context_surfaces_despite_privacy_policy_crowding():
    svc = _service()
    products = [_product_chunk("Black Wrap Maxi Dress", 0.705), _product_chunk("Floral Print Shirt Dress", 0.688)]

    def fake_rpc(fn, params):
        assert fn == "match_brand_rag_chunks"
        return _mixed_corpus_rpc(params.get("p_brand_id"), products, params["match_count"])

    with patch("src.services.brand_knowledge_service.supabase_rpc", side_effect=fake_rpc), \
         patch.object(svc, "_get_embedding", return_value=[0.1, 0.2, 0.3]):
        context = run(svc.get_brand_context(brand_id=BRAND_A, query="What products do you sell?", top_k=5))

    assert "Black Wrap Maxi Dress" in context
    assert "Floral Print Shirt Dress" in context


def test_privacy_policy_never_appears_in_retrieved_context():
    svc = _service()
    products = [_product_chunk("Emerald Green Wrap Dress", 0.705)]

    def fake_rpc(fn, params):
        return _mixed_corpus_rpc(params.get("p_brand_id"), products, params["match_count"])

    with patch("src.services.brand_knowledge_service.supabase_rpc", side_effect=fake_rpc), \
         patch.object(svc, "_get_embedding", return_value=[0.1, 0.2, 0.3]):
        context = run(svc.get_brand_context(brand_id=BRAND_A, query="What products do you sell?", top_k=5))

    assert "Privacy policy" not in context
    assert "device data" not in context


def test_genuinely_useful_policy_is_not_filtered_out():
    """Only the privacy/terms-of-service denylist is excluded - a real
    refund/shipping policy must still be retrievable and usable."""
    svc = _service()

    def fake_rpc(fn, params):
        return _mixed_corpus_rpc(params.get("p_brand_id"), [], params["match_count"])

    with patch("src.services.brand_knowledge_service.supabase_rpc", side_effect=fake_rpc), \
         patch.object(svc, "_get_embedding", return_value=[0.1, 0.2, 0.3]):
        context = run(svc.get_brand_context(brand_id=BRAND_A, query="What is your return policy?", top_k=5))

    assert "Returns accepted within 30 days" in context


# ── 2. Another brand's products are never retrieved ──────────────────────────

def test_another_brands_products_never_retrieved():
    svc = _service()
    products = [_product_chunk("Brand A Exclusive Dress", 0.705)]

    def fake_rpc(fn, params):
        return _mixed_corpus_rpc(params.get("p_brand_id"), products, params["match_count"])

    with patch("src.services.brand_knowledge_service.supabase_rpc", side_effect=fake_rpc), \
         patch.object(svc, "_get_embedding", return_value=[0.1, 0.2, 0.3]):
        context = run(svc.get_brand_context(brand_id=BRAND_B, query="What products do you sell?", top_k=5))

    assert "Brand A Exclusive Dress" not in context
    assert context == ""


# ── Unit coverage for the filter helper itself ────────────────────────────────

def test_is_low_value_policy_chunk_matches_privacy_and_terms():
    assert _is_low_value_policy_chunk({"metadata": {"type": "shopify_policy", "policy_title": "Privacy Policy"}})
    assert _is_low_value_policy_chunk({"metadata": {"type": "shopify_policy", "policy_title": "Terms of Service"}})
    assert not _is_low_value_policy_chunk({"metadata": {"type": "shopify_policy", "policy_title": "Refund Policy"}})
    assert not _is_low_value_policy_chunk({"metadata": {"type": "shopify_product_batch"}})
    assert not _is_low_value_policy_chunk({"source_name": "Products (batch 1)"})


# ── 4. Missing product knowledge fails honestly, doesn't falsely "pass" ─────

def test_test_reply_endpoint_forwards_escalate_signal_for_honest_failure():
    """v2_brands.py's /test-reply must surface the agent's own escalate flag
    so the onboarding UI can tell a genuinely ungrounded reply apart from a
    real pass - not just whether the HTTP call itself succeeded."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.routes.v2_brands import router as v2_brands_router
    from src.api.middleware.tenant_auth import get_current_tenant, TenantContext

    app = FastAPI()
    app.include_router(v2_brands_router, prefix="/api/v2")
    test_client = TestClient(app)

    tenant_id, brand_id = "tenant-x", "brand-x"

    def fake_select(table, params=None):
        if table == "brands" and (params or {}).get("id") == f"eq.{brand_id}":
            return [{"id": brand_id, "tenant_id": tenant_id}]
        return []

    async def fake_process_customer_query(**kwargs):
        # Simulates the agent's own honesty backstop: no grounded knowledge
        # base match -> confidence dropped below the escalate threshold.
        return {
            "reply_body": "I don't have the full product list right here, could you check the store directly?",
            "confidence_score": 65,
            "escalate": True,
            "status": "escalated",
        }

    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(tenant_id=tenant_id, email="merchant@example.com")
    try:
        with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
             patch("src.agent.customer_success_agent.customer_success_agent.process_customer_query", side_effect=fake_process_customer_query), \
             patch("src.api.routes.v2_brands.supabase_service.log_onboarding_event"):
            resp = test_client.post(f"/api/v2/brands/{brand_id}/test-reply", json={"message": "What products do you sell?"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["escalate"] is True, "escalate must be forwarded so the UI never shows a false 'Test passed'"


def test_test_reply_endpoint_does_not_escalate_on_a_confident_grounded_reply():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.routes.v2_brands import router as v2_brands_router
    from src.api.middleware.tenant_auth import get_current_tenant, TenantContext

    app = FastAPI()
    app.include_router(v2_brands_router, prefix="/api/v2")
    test_client = TestClient(app)

    tenant_id, brand_id = "tenant-y", "brand-y"

    def fake_select(table, params=None):
        if table == "brands" and (params or {}).get("id") == f"eq.{brand_id}":
            return [{"id": brand_id, "tenant_id": tenant_id}]
        return []

    async def fake_process_customer_query(**kwargs):
        return {
            "reply_body": "We sell the Black Wrap Maxi Dress, the Emerald Green Wrap Dress, and more!",
            "confidence_score": 90,
            "escalate": False,
            "status": "auto_resolved",
        }

    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(tenant_id=tenant_id, email="merchant@example.com")
    try:
        with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
             patch("src.agent.customer_success_agent.customer_success_agent.process_customer_query", side_effect=fake_process_customer_query), \
             patch("src.api.routes.v2_brands.supabase_service.log_onboarding_event"):
            resp = test_client.post(f"/api/v2/brands/{brand_id}/test-reply", json={"message": "What products do you sell?"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["escalate"] is False
