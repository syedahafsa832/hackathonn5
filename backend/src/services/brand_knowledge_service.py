"""
Brand Knowledge Base Service
============================
Per-brand RAG knowledge base management.
"""

import os
import logging
import uuid
import re
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from openai import OpenAI
from src.lib.supabase_client import (
    supabase_select,
    supabase_insert,
    supabase_update,
    supabase_delete,
    supabase_rpc
)

logger = logging.getLogger(__name__)


class BrandKnowledgeService:
    """
    Service for managing per-brand knowledge bases.

    Features:
    - Text chunking and embedding generation
    - Brand-isolated storage
    - Vector search for RAG retrieval
    """

    def __init__(self):
        api_key = os.getenv("MISTRAL_API_KEY") or os.getenv("OPENAI_API_KEY")
        if api_key:
            self.ai_client = OpenAI(
                api_key=api_key,
                base_url=os.getenv("MISTRAL_API_BASE_URL", "https://api.mistral.ai/v1")
            )
        else:
            logger.warning("No API key found for embeddings")
            self.ai_client = None

        self.embedding_model = os.getenv("EMBEDDING_MODEL", "mistral-embed")
        self.chunk_size = 1000  # Characters per chunk
        self.chunk_overlap = 200  # Overlap between chunks

    def _chunk_text(self, text: str, source_name: str) -> List[Dict[str, Any]]:
        """Split text into overlapping chunks for embedding."""
        text = text.strip()
        if not text:
            return []

        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', text)

        chunks = []
        current_chunk = ""
        chunk_index = 0

        for sentence in sentences:
            if len(current_chunk) + len(sentence) > self.chunk_size:
                if current_chunk:
                    chunks.append({
                        "content": current_chunk.strip(),
                        "source_name": source_name,
                        "chunk_index": chunk_index
                    })
                    chunk_index += 1

                    overlap_start = max(0, len(current_chunk) - self.chunk_overlap)
                    current_chunk = current_chunk[overlap_start:] + " " + sentence
                else:
                    for i in range(0, len(sentence), self.chunk_size - self.chunk_overlap):
                        chunk_text = sentence[i:i + self.chunk_size]
                        if chunk_text.strip():
                            chunks.append({
                                "content": chunk_text.strip(),
                                "source_name": source_name,
                                "chunk_index": chunk_index
                            })
                            chunk_index += 1
                    current_chunk = ""
            else:
                current_chunk += " " + sentence

        if current_chunk.strip():
            chunks.append({
                "content": current_chunk.strip(),
                "source_name": source_name,
                "chunk_index": chunk_index
            })

        return chunks

    def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Generate embedding for text using Mistral."""
        if not self.ai_client:
            logger.error("AI client not initialized")
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

    async def upload_text(
        self,
        brand_id: str,
        name: str,
        content: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Upload text content to brand's knowledge base.

        Args:
            brand_id: The brand UUID
            name: Source name/title
            content: Text content to embed
            user_id: ID of user uploading
            metadata: Optional metadata

        Returns:
            Result dict with source_id, chunk_count, status
        """
        try:
            # Create source record
            source_id = str(uuid.uuid4())
            source_record = {
                "id": source_id,
                "brand_id": brand_id,
                "name": name,
                "source_type": "text",
                "status": "processing",
                "created_by": user_id,
                "metadata": metadata or {}
            }
            supabase_insert("knowledge_base_sources", source_record)
            logger.info(f"[KB] Created source: {source_id} for brand {brand_id}")

            # Chunk the text
            chunks = self._chunk_text(content, name)
            if not chunks:
                supabase_update("knowledge_base_sources", {"id": f"eq.{source_id}"}, {
                    "status": "failed",
                    "error_message": "No content to process"
                })
                return {"success": False, "error": "No content to process"}

            logger.info(f"[KB] Created {len(chunks)} chunks")

            # Generate embeddings and store
            successful_chunks = 0
            total_tokens = 0

            for chunk in chunks:
                embedding = self._get_embedding(chunk["content"])
                if not embedding:
                    logger.warning(f"[KB] Failed to embed chunk {chunk['chunk_index']}")
                    continue

                chunk_record = {
                    "id": str(uuid.uuid4()),
                    "brand_id": brand_id,
                    "source_id": source_id,
                    "content": chunk["content"],
                    "embedding": embedding,
                    "source_name": chunk["source_name"],
                    "chunk_index": chunk["chunk_index"],
                    "token_count": len(chunk["content"].split()),  # Rough estimate
                    "metadata": metadata or {"type": "brand_knowledge"}
                }
                supabase_insert("rag_chunks", chunk_record)
                successful_chunks += 1
                total_tokens += chunk_record["token_count"]

            # Update source status
            if successful_chunks > 0:
                supabase_update("knowledge_base_sources", {"id": f"eq.{source_id}"}, {
                    "status": "completed",
                    "chunk_count": successful_chunks,
                    "total_tokens": total_tokens
                })
                logger.info(f"[KB] Stored {successful_chunks} chunks for brand {brand_id}")
                return {
                    "success": True,
                    "source_id": source_id,
                    "chunk_count": successful_chunks,
                    "total_tokens": total_tokens,
                    "status": "completed"
                }
            else:
                supabase_update("knowledge_base_sources", {"id": f"eq.{source_id}"}, {
                    "status": "failed",
                    "error_message": "Failed to generate embeddings"
                })
                return {"success": False, "error": "Failed to generate embeddings"}

        except Exception as e:
            logger.error(f"[KB] Upload error: {e}")
            return {"success": False, "error": str(e)}

    async def get_sources(self, brand_id: str) -> List[Dict[str, Any]]:
        """Get all knowledge base sources for a brand."""
        try:
            sources = supabase_select(
                "knowledge_base_sources",
                {
                    "brand_id": f"eq.{brand_id}",
                    "order": "created_at.desc"
                }
            )
            return sources or []
        except Exception as e:
            logger.error(f"[KB] Error fetching sources: {e}")
            return []

    async def delete_source(self, brand_id: str, source_id: str) -> Dict[str, Any]:
        """Delete a knowledge base source and its chunks."""
        try:
            # Verify ownership
            sources = supabase_select("knowledge_base_sources", {
                "id": f"eq.{source_id}",
                "brand_id": f"eq.{brand_id}"
            })

            if not sources:
                return {"success": False, "error": "Source not found"}

            # Delete chunks first
            supabase_delete("rag_chunks", {
                "source_id": f"eq.{source_id}"
            })

            # Delete source record
            supabase_delete("knowledge_base_sources", {"id": f"eq.{source_id}"})

            logger.info(f"[KB] Deleted source {source_id}")
            return {"success": True}

        except Exception as e:
            logger.error(f"[KB] Delete error: {e}")
            return {"success": False, "error": str(e)}

    async def get_brand_context(
        self,
        brand_id: str,
        query: str,
        top_k: int = 5
    ) -> str:
        """
        Get relevant context from brand's knowledge base.

        Args:
            brand_id: The brand UUID
            query: Search query
            top_k: Number of results

        Returns:
            Formatted context string
        """
        try:
            if not self.ai_client:
                logger.warning("AI client not initialized")
                return ""

            # Generate query embedding
            embedding = self._get_embedding(query)
            if not embedding:
                return ""

            # Search brand's knowledge base using RPC function
            results = supabase_rpc("match_brand_rag_chunks", {
                "p_brand_id": brand_id,
                "query_embedding": embedding,
                "match_threshold": 0.5,
                "match_count": top_k
            })

            if not results:
                logger.info(f"[KB] No matching context for brand {brand_id}")
                return ""

            # Format context
            context_parts = []
            for res in results:
                source = res.get("source_name", "Knowledge Base")
                content = res.get("content", "")
                similarity = res.get("similarity", 0)
                logger.debug(f"[KB] Match: {source} (similarity: {similarity:.2f})")
                context_parts.append(f"[{source}]:\n{content}")

            return "\n\n---\n\n".join(context_parts)

        except Exception as e:
            logger.error(f"[KB] Context retrieval error: {e}")
            return ""

    async def search_knowledge(
        self,
        brand_id: str,
        query: str,
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search brand's knowledge base and return raw results.

        Used for displaying search results in UI.
        """
        try:
            if not self.ai_client:
                return []

            embedding = self._get_embedding(query)
            if not embedding:
                return []

            results = supabase_rpc("match_brand_rag_chunks", {
                "p_brand_id": brand_id,
                "query_embedding": embedding,
                "match_threshold": 0.3,
                "match_count": top_k
            })

            return results or []

        except Exception as e:
            logger.error(f"[KB] Search error: {e}")
            return []


# Global instance
brand_knowledge_service = BrandKnowledgeService()
