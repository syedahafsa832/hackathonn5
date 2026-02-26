import os
import asyncio
import logging
from typing import List, Dict, Any, Optional
from openai import OpenAI
from src.lib.supabase_client import supabase_rpc, supabase_select

logger = logging.getLogger(__name__)

class RAGEngine:
    """
    RAG Retrieval Engine for Aurelio & Finch.
    Performs metadata-filtered vector search in Supabase.
    """

    def __init__(self):
        self.ai_client = OpenAI(
            api_key=os.getenv("MISTRAL_API_KEY"),
            base_url=os.getenv("MISTRAL_API_BASE_URL", "https://api.mistral.ai/v1")
        )
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "mistral-embed")
        self.top_k = 3

    async def get_relevant_context(self, query: str, filters: Optional[Dict[str, Any]] = None) -> str:
        """
        Embed the query and perform a vector search with metadata filtering.
        """
        try:
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
                logger.warning(f"No RAG context found for query: {query}")
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
