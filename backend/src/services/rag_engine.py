import os
import asyncio
import logging
from typing import List, Dict, Any, Optional

# Set OPENAI_API_KEY for compatibility with Mistral's OpenAI-compatible API
if not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.getenv("MISTRAL_API_KEY", "")

from openai import OpenAI
from src.lib.supabase_client import supabase_rpc, supabase_select

logger = logging.getLogger(__name__)

class RAGEngine:
    """
    RAG Retrieval Engine with Multi-Tenant Support.
    Performs metadata-filtered vector search in Supabase.
    """

    def __init__(self):
        api_key = os.getenv("MISTRAL_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("No MISTRAL_API_KEY or OPENAI_API_KEY found, RAG will have limited functionality")
            self.ai_client = None
        else:
            self.ai_client = OpenAI(
                api_key=api_key,
                base_url=os.getenv("MISTRAL_API_BASE_URL", "https://api.mistral.ai/v1")
            )
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "mistral-embed")
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
            if not self.ai_client:
                logger.warning("AI client not initialized, returning empty context")
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
        Embed the query and perform a vector search with metadata filtering.
        If tenant_id is provided, uses tenant-specific search first.
        """
        try:
            # If tenant_id provided, try tenant-specific search first
            if tenant_id:
                tenant_context = await self.get_tenant_context(query, tenant_id)
                if tenant_context:
                    return tenant_context

            # 1. Generate Embedding
            embedding = await self._get_embedding(query)
            if not embedding:
                return ""

            # 2. Perform Vector Search via Supabase RPC (match_rag_chunks)
            # filters can be used for metadata filtering in the SQL function
            rpc_params = {
                "query_embedding": embedding,
                "match_threshold": 0.5,
                "match_count": self.top_k,
                "filter_metadata": filters or {}
            }

            # We assume a Supabase RPC function 'match_rag_chunks' is defined
            results = supabase_rpc("match_rag_chunks", rpc_params)

            if not results:
                logger.info(f"[RAG] No context found for query: {query[:50]}...")
                return ""

            # 3. Format context
            context_parts = []
            for res in results:
                context_parts.append(f"Source: {res.get('metadata', {}).get('type', 'general')}\nContent: {res.get('content')}")

            return "\n\n---\n\n".join(context_parts)

        except Exception as e:
            logger.error(f"RAG Retrieval Error: {e}")
            return ""

    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Mistral Embedding call."""
        if not self.ai_client:
            logger.warning("AI client not initialized, returning empty embedding")
            return None
        try:
            response = self.ai_client.embeddings.create(
                input=[text],
                model=self.embedding_model
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Embedding error: {e}")
            return None

rag_engine = RAGEngine()
