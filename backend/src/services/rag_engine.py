import os
import logging
import time
from typing import List, Dict, Any, Optional

# Set OPENAI_API_KEY for compatibility with Mistral's OpenAI-compatible API
if not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.getenv("MISTRAL_API_KEY", "")

from src.lib.supabase_client import supabase_rpc, supabase_select
from src.services.ai_provider_manager import ai_provider_manager

logger = logging.getLogger(__name__)

# Cache of tenant_id -> has_docs (bool), 5 min TTL, to avoid a DB roundtrip on every message
_kb_doc_cache: Dict[str, tuple] = {}
_KB_CACHE_TTL_SECONDS = 300

class RAGEngine:
    """
    RAG Retrieval Engine with Multi-Tenant Support.
    Performs metadata-filtered vector search in Supabase.
    """

    def __init__(self):
        if not ai_provider_manager.mistral_providers:
            logger.warning("No Mistral keys configured, RAG will have limited functionality")
        self.top_k = 3

    async def get_tenant_context(self, query: str, tenant_id: str, top_k: int = 3) -> str:
        """
        Get relevant context from a specific tenant's knowledge base.

        Args:
            query: The search query
            tenant_id: The tenant's UUID
            top_k: Number of results to return

        Returns:
            Formatted context string from tenant's knowledge base
        """
        try:
            if not self._tenant_has_kb_docs(tenant_id):
                logger.info(f"[RAG] Tenant {tenant_id} has no knowledge base documents — skipping embedding call")
                return ""

            # Generate embedding for query
            embedding = await self._get_embedding(query)
            if not embedding:
                return ""

            # Search tenant's knowledge base
            results = supabase_rpc("match_tenant_rag_chunks", {
                "p_tenant_id": tenant_id,
                "query_embedding": embedding,
                "match_threshold": 0.5,
                "match_count": top_k
            })

            if not results:
                logger.info(f"[RAG] No matching context for tenant {tenant_id}")
                return ""

            # Format context
            context_parts = []
            for res in results:
                source = res.get("source_name", "Knowledge Base")
                content = res.get("content", "")
                similarity = res.get("similarity", 0)
                logger.info(f"[RAG] Found match: {source} (similarity: {similarity:.2f})")
                context_parts.append(f"[{source}]: {content}")

            return "\n\n".join(context_parts)

        except Exception as e:
            logger.error(f"[RAG] Tenant context error: {e}")
            return ""

    async def get_relevant_context(self, query: str, filters: Optional[Dict[str, Any]] = None, tenant_id: Optional[str] = None) -> str:
        """
        Tenant-scoped vector search only. If tenant_id is provided, uses
        get_tenant_context(); otherwise returns no context.

        This used to fall through to an unscoped "match_rag_chunks" RPC
        (no tenant/brand filter at all) whenever the tenant-scoped search
        came back empty - which happens on any ordinary no-match query, not
        just errors. That meant a ordinary empty result could silently
        return another tenant's knowledge-base content. This module is not
        called from any live code path today (the agent uses
        brand_knowledge_service.get_brand_context, which is correctly
        brand-scoped) - removed rather than fixed-in-place, since there is
        no legitimate case where returning cross-tenant content is correct.
        """
        try:
            if not tenant_id:
                logger.warning("[RAG] get_relevant_context called without tenant_id — refusing to run an unscoped search")
                return ""
            return await self.get_tenant_context(query, tenant_id)

        except Exception as e:
            logger.error(f"RAG Retrieval Error: {e}")
            return ""

    def _tenant_has_kb_docs(self, tenant_id: str) -> bool:
        """Check (with a short-lived cache) whether this tenant has any knowledge base
        sources at all — skips the Mistral embedding call entirely when there's nothing to search."""
        now = time.time()
        cached = _kb_doc_cache.get(tenant_id)
        if cached and (now - cached[1]) < _KB_CACHE_TTL_SECONDS:
            return cached[0]
        try:
            rows = supabase_select("knowledge_base_sources", {
                "tenant_id": f"eq.{tenant_id}",
                "limit": "1",
            })
            has_docs = bool(rows)
        except Exception as e:
            logger.warning(f"[RAG] KB doc count check failed for tenant {tenant_id}: {e} — assuming docs exist")
            has_docs = True  # fail open so RAG isn't silently disabled on a transient error
        _kb_doc_cache[tenant_id] = (has_docs, now)
        return has_docs

    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Generate embedding for text. Rotates across every configured
        Mistral key with a bounded per-attempt timeout — see
        ai_provider_manager.create_embedding for the shared policy."""
        return await ai_provider_manager.create_embedding(text=text)

rag_engine = RAGEngine()
