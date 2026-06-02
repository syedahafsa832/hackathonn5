"""
Knowledge Base Service for Multi-Tenant RAG

Handles:
- Text chunking and embedding generation
- Tenant-isolated storage in Supabase
- Knowledge base source management
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


class KnowledgeBaseService:
    """Service for managing tenant knowledge bases."""

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
        """
        Split text into overlapping chunks for embedding.
        Uses sentence boundaries when possible.
        """
        # Clean text
        text = text.strip()
        if not text:
            return []

        # Split into sentences first
        sentences = re.split(r'(?<=[.!?])\s+', text)

        chunks = []
        current_chunk = ""
        chunk_index = 0

        for sentence in sentences:
            # If adding this sentence would exceed chunk size
            if len(current_chunk) + len(sentence) > self.chunk_size:
                if current_chunk:
                    chunks.append({
                        "content": current_chunk.strip(),
                        "source_name": source_name,
                        "chunk_index": chunk_index
                    })
                    chunk_index += 1

                    # Keep overlap from end of previous chunk
                    overlap_start = max(0, len(current_chunk) - self.chunk_overlap)
                    current_chunk = current_chunk[overlap_start:] + " " + sentence
                else:
                    # Single sentence larger than chunk size - split it
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

        # Don't forget the last chunk
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
        tenant_id: str,
        name: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Upload text content to tenant's knowledge base.

        Args:
            tenant_id: The tenant UUID
            name: Source name/title
            content: Text content to embed
            metadata: Optional metadata for chunks

        Returns:
            Result dict with source_id, chunk_count, status
        """
        try:
            # 1. Create source record
            source_id = str(uuid.uuid4())
            source_record = {
                "id": source_id,
                "tenant_id": tenant_id,
                "name": name,
                "source_type": "text",
                "status": "processing"
            }
            supabase_insert("knowledge_base_sources", source_record)
            logger.info(f"[KB] Created source record: {source_id}")

            # 2. Chunk the text
            chunks = self._chunk_text(content, name)
            if not chunks:
                supabase_update("knowledge_base_sources", {"id": f"eq.{source_id}"}, {
                    "status": "failed",
                    "error_message": "No content to process"
                })
                return {"success": False, "error": "No content to process"}

            logger.info(f"[KB] Created {len(chunks)} chunks from text")

            # 3. Generate embeddings and store
            successful_chunks = 0
            for chunk in chunks:
                embedding = self._get_embedding(chunk["content"])
                if not embedding:
                    logger.warning(f"[KB] Failed to embed chunk {chunk['chunk_index']}")
                    continue

                chunk_record = {
                    "id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "content": chunk["content"],
                    "embedding": embedding,
                    "source_name": chunk["source_name"],
                    "chunk_index": chunk["chunk_index"],
                    "metadata": metadata or {"type": "brand_knowledge"}
                }
                supabase_insert("rag_chunks", chunk_record)
                successful_chunks += 1

            # 4. Update source status
            if successful_chunks > 0:
                supabase_update("knowledge_base_sources", {"id": f"eq.{source_id}"}, {
                    "status": "completed",
                    "chunk_count": successful_chunks
                })
                logger.info(f"[KB] Successfully stored {successful_chunks} chunks")
                return {
                    "success": True,
                    "source_id": source_id,
                    "chunk_count": successful_chunks,
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

    async def get_sources(self, tenant_id: str) -> List[Dict[str, Any]]:
        """Get all knowledge base sources for a tenant."""
        try:
            sources = supabase_select(
                "knowledge_base_sources",
                {"tenant_id": f"eq.{tenant_id}", "order": "created_at.desc"}
            )
            return sources or []
        except Exception as e:
            logger.error(f"[KB] Error fetching sources: {e}")
            return []

    async def delete_source(self, tenant_id: str, source_id: str) -> Dict[str, Any]:
        """Delete a knowledge base source and its chunks."""
        try:
            # Verify ownership
            sources = supabase_select("knowledge_base_sources", {
                "id": f"eq.{source_id}",
                "tenant_id": f"eq.{tenant_id}"
            })

            if not sources:
                return {"success": False, "error": "Source not found"}

            source = sources[0]
            source_name = source.get("name")

            # Delete chunks first (by source_name and tenant_id)
            supabase_delete("rag_chunks", {
                "tenant_id": f"eq.{tenant_id}",
                "source_name": f"eq.{source_name}"
            })

            # Delete source record
            supabase_delete("knowledge_base_sources", {"id": f"eq.{source_id}"})

            logger.info(f"[KB] Deleted source {source_id} and its chunks")
            return {"success": True}

        except Exception as e:
            logger.error(f"[KB] Delete error: {e}")
            return {"success": False, "error": str(e)}

    async def get_tenant_context(
        self,
        tenant_id: str,
        query: str,
        top_k: int = 3
    ) -> str:
        """
        Get relevant context from tenant's knowledge base.
        Used by the AI agent when generating responses.
        """
        try:
            if not self.ai_client:
                return ""

            # Generate query embedding
            embedding = self._get_embedding(query)
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
                logger.info(f"[KB] No matching context for tenant {tenant_id}")
                return ""

            # Format context
            context_parts = []
            for res in results:
                source = res.get("source_name", "Knowledge Base")
                content = res.get("content", "")
                context_parts.append(f"[{source}]: {content}")

            return "\n\n".join(context_parts)

        except Exception as e:
            logger.error(f"[KB] Context retrieval error: {e}")
            return ""


# Global instance
knowledge_base_service = KnowledgeBaseService()
