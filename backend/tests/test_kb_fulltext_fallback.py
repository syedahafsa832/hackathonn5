"""
Focused tests for making embeddings optional for ordinary merchant
Knowledge Base / policy retrieval (tResolv retrieval-architecture fix).

Root cause: brand_knowledge_service.get_brand_context_with_status() had
exactly one retrieval path - Mistral embedding -> match_brand_rag_chunks
(pgvector). Any embedding-provider outage (rate limit, timeout, exhausted
quota) or a failure in the vector RPC itself made EVERY KB/policy question
("what's your return policy?", "do you ship internationally?") come back
with zero context, even though the same rag_chunks.content the vector
search would have searched was sitting right there in Postgres.

Fix: a Postgres full-text search fallback (match_brand_rag_chunks_fts,
migrations/057_kb_fulltext_search_fallback.sql) over the SAME rag_chunks
table, same brand scoping, same low-value-policy-chunk filtering. Semantic
(vector) search stays the primary path when the embedding provider is
healthy - this only engages when embedding generation returns None or the
vector RPC itself raises. Not a second knowledge base, not a second
embedding provider, no new table.
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


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _service():
    return BrandKnowledgeService()


# ── Embedding unavailable -> falls back to full-text search ────────────────

def test_embedding_unavailable_falls_back_to_fts_and_finds_a_match():
    svc = _service()
    rpc_calls = []

    def fake_rpc(fn, params):
        rpc_calls.append(fn)
        assert fn == "match_brand_rag_chunks_fts"
        assert params["p_brand_id"] == BRAND_A
        assert params["query_text"] == "what is your return policy?"
        return [{"source_name": "Return Policy", "content": "Returns accepted within 30 days.", "similarity": 0.4}]

    with patch.object(svc, "_get_embedding", new=AsyncMock(return_value=None)), \
         patch("src.services.brand_knowledge_service.supabase_rpc", side_effect=fake_rpc):
        context, status = run(svc.get_brand_context_with_status(BRAND_A, "what is your return policy?"))

    assert rpc_calls == ["match_brand_rag_chunks_fts"]
    assert status == "ok"
    assert "Returns accepted within 30 days." in context


def test_embedding_unavailable_and_fts_finds_nothing_is_no_match_not_unavailable():
    """FTS ran successfully (returned []) - a real "nothing relevant" answer,
    distinct from a retrieval failure."""
    svc = _service()

    with patch.object(svc, "_get_embedding", new=AsyncMock(return_value=None)), \
         patch("src.services.brand_knowledge_service.supabase_rpc", return_value=[]):
        context, status = run(svc.get_brand_context_with_status(BRAND_A, "do you sell moon rocks?"))

    assert context == ""
    assert status == "no_match"


def test_embedding_unavailable_and_fts_rpc_also_fails_reports_unavailable():
    svc = _service()

    with patch.object(svc, "_get_embedding", new=AsyncMock(return_value=None)), \
         patch("src.services.brand_knowledge_service.supabase_rpc", side_effect=Exception("connection reset")):
        context, status = run(svc.get_brand_context_with_status(BRAND_A, "what is your return policy?"))

    assert context == ""
    assert status == "unavailable"


# ── Vector RPC itself failing (embedding succeeded) also falls back ────────

def test_vector_rpc_failure_falls_back_to_fts():
    svc = _service()
    calls = []

    def fake_rpc(fn, params):
        calls.append(fn)
        if fn == "match_brand_rag_chunks":
            raise Exception("relation rag_chunks tenant_id mismatch")
        assert fn == "match_brand_rag_chunks_fts"
        return [{"source_name": "Shipping Policy", "content": "We ship worldwide.", "similarity": 0.3}]

    with patch.object(svc, "_get_embedding", new=AsyncMock(return_value=[0.1] * 1024)), \
         patch("src.services.brand_knowledge_service.supabase_rpc", side_effect=fake_rpc):
        context, status = run(svc.get_brand_context_with_status(BRAND_A, "do you ship internationally?"))

    assert calls == ["match_brand_rag_chunks", "match_brand_rag_chunks_fts"]
    assert status == "ok"
    assert "We ship worldwide." in context


# ── A successful vector search never touches FTS, even with zero results ───

def test_successful_vector_search_never_falls_back_to_fts_even_with_no_matches():
    """Embeddings are healthy and the vector RPC ran fine - a genuine
    no-match must stay a no-match, never trigger a redundant FTS call."""
    svc = _service()
    calls = []

    def fake_rpc(fn, params):
        calls.append(fn)
        return []

    with patch.object(svc, "_get_embedding", new=AsyncMock(return_value=[0.1] * 1024)), \
         patch("src.services.brand_knowledge_service.supabase_rpc", side_effect=fake_rpc):
        context, status = run(svc.get_brand_context_with_status(BRAND_A, "do you sell moon rocks?"))

    assert calls == ["match_brand_rag_chunks"]
    assert status == "no_match"
    assert context == ""


# ── Brand isolation is preserved on the fallback path too ──────────────────

def test_fts_fallback_is_brand_scoped():
    svc = _service()

    def fake_rpc(fn, params):
        assert fn == "match_brand_rag_chunks_fts"
        if params["p_brand_id"] == BRAND_A:
            return [{"source_name": "Return Policy", "content": "Returns accepted within 30 days.", "similarity": 0.4}]
        return []

    with patch.object(svc, "_get_embedding", new=AsyncMock(return_value=None)), \
         patch("src.services.brand_knowledge_service.supabase_rpc", side_effect=fake_rpc):
        context_a, _ = run(svc.get_brand_context_with_status(BRAND_A, "what is your return policy?"))
        context_b, _ = run(svc.get_brand_context_with_status(BRAND_B, "what is your return policy?"))

    assert "Returns accepted within 30 days." in context_a
    assert context_b == ""


# ── Fallback context is bounded (top_k), not the whole KB ──────────────────

def test_fts_fallback_context_is_bounded_to_top_k():
    svc = _service()
    many_results = [
        {"source_name": f"Doc {i}", "content": f"Content {i}", "similarity": 0.5}
        for i in range(20)
    ]

    with patch.object(svc, "_get_embedding", new=AsyncMock(return_value=None)), \
         patch("src.services.brand_knowledge_service.supabase_rpc", return_value=many_results):
        context, status = run(svc.get_brand_context_with_status(BRAND_A, "tell me everything", top_k=3))

    assert status == "ok"
    assert context.count("[Doc") == 3


# ── Low-value policy chunks (Privacy Policy / ToS) are filtered on the ─────
#    fallback path exactly as they are on the vector path

def test_fts_fallback_still_drops_low_value_policy_chunks():
    svc = _service()
    results = [
        {"source_name": "Privacy Policy", "content": "Legal boilerplate.", "similarity": 0.9,
         "metadata": {"type": "shopify_policy", "policy_title": "Privacy Policy"}},
        {"source_name": "Return Policy", "content": "Returns accepted within 30 days.", "similarity": 0.4},
    ]

    with patch.object(svc, "_get_embedding", new=AsyncMock(return_value=None)), \
         patch("src.services.brand_knowledge_service.supabase_rpc", return_value=results):
        context, status = run(svc.get_brand_context_with_status(BRAND_A, "what is your return policy?"))

    assert status == "ok"
    assert "Returns accepted within 30 days." in context
    assert "Legal boilerplate." not in context


# ── get_brand_context() (the legacy wrapper every real caller uses) also ───
#    benefits from the fallback transparently

def test_legacy_get_brand_context_wrapper_also_uses_fts_fallback():
    svc = _service()

    def fake_rpc(fn, params):
        assert fn == "match_brand_rag_chunks_fts"
        return [{"source_name": "Warranty Policy", "content": "12-month warranty on all items.", "similarity": 0.5}]

    with patch.object(svc, "_get_embedding", new=AsyncMock(return_value=None)), \
         patch("src.services.brand_knowledge_service.supabase_rpc", side_effect=fake_rpc):
        context = run(svc.get_brand_context(BRAND_A, "what's your warranty policy?"))

    assert "12-month warranty on all items." in context
