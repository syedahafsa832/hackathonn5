"""
Brand Knowledge Base Service
============================
Per-brand RAG knowledge base management.
"""

import logging
import uuid
import re
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from src.lib.supabase_client import (
    supabase_select,
    supabase_insert,
    supabase_update,
    supabase_delete,
    supabase_rpc
)
from src.services.ai_provider_manager import ai_provider_manager

logger = logging.getLogger(__name__)

# Shopify's Privacy Policy / Terms of Service are dense, generic legal
# boilerplate that shopify_import_service imports like any other policy
# (see its own docstring) but that never actually helps answer a customer
# question. For a small store, a single Privacy Policy import can produce
# far more chunks than every other source combined (e.g. 24 vs 5 product
# chunks observed live) - since match_brand_rag_chunks ranks purely by raw
# cosine similarity with no per-source cap, those chunks statistically
# dominate the top-k for almost any short/generic query and crowd out the
# actually-relevant handful of chunks (product catalog, real policies)
# entirely. Excluded from retrieval only - the rows/chunks themselves are
# left untouched, so no data is deleted or migrated.
_LOW_VALUE_POLICY_TITLES = ("privacy policy", "terms of service", "terms & conditions", "terms and conditions")


def _is_low_value_policy_chunk(result: Dict[str, Any]) -> bool:
    metadata = result.get("metadata") or {}
    if metadata.get("type") != "shopify_policy":
        return False
    title = (metadata.get("policy_title") or result.get("source_name") or "").strip().lower()
    return any(t in title for t in _LOW_VALUE_POLICY_TITLES)


class BrandKnowledgeService:
    """
    Service for managing per-brand knowledge bases.

    Features:
    - Text chunking and embedding generation
    - Brand-isolated storage
    - Vector search for RAG retrieval
    """

    def __init__(self):
        if not ai_provider_manager.mistral_providers:
            logger.warning("No API key found for embeddings")

        self.chunk_size = 1000  # Characters per chunk
        self.chunk_overlap = 200  # Overlap between chunks

    def _get_tenant_id(self, brand_id: str) -> Optional[str]:
        rows = supabase_select("brands", {"id": f"eq.{brand_id}"})
        return rows[0].get("tenant_id") if rows else None

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

    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Generate embedding for text. Rotates across every configured
        Mistral key with a bounded per-attempt timeout — see
        ai_provider_manager.create_embedding for the shared policy. Returns
        None (never raises) if every key fails, so callers degrade to no
        context instead of blocking or crashing."""
        return await ai_provider_manager.create_embedding(text=text)

    async def upload_text(
        self,
        brand_id: str,
        name: str,
        content: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source_type: str = "text"
    ) -> Dict[str, Any]:
        """
        Upload text content to brand's knowledge base.

        Args:
            brand_id: The brand UUID
            name: Source name/title
            content: Text content to embed
            user_id: ID of user uploading
            metadata: Optional metadata
            source_type: 'text' (manual paste, default) or 'shopify_sync' (auto-import)

        Returns:
            Result dict with source_id, chunk_count, status
        """
        try:
            # tenant_id is still NOT NULL on knowledge_base_sources/rag_chunks
            # (the live schema never actually migrated off it onto brand_id
            # alone), so every insert needs both.
            tenant_id = self._get_tenant_id(brand_id)
            if not tenant_id:
                return {"success": False, "error": "Could not resolve tenant for this brand"}

            # Create source record
            source_id = str(uuid.uuid4())
            source_record = {
                "id": source_id,
                "brand_id": brand_id,
                "tenant_id": tenant_id,
                "name": name,
                "source_type": source_type,
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
                # _get_embedding is itself non-blocking (each key attempt
                # runs off the event loop via ai_provider_manager's
                # call_with_limit) - no extra to_thread needed here.
                embedding = await self._get_embedding(chunk["content"])
                if not embedding:
                    logger.warning(f"[KB] Failed to embed chunk {chunk['chunk_index']}")
                    continue

                chunk_record = {
                    "id": str(uuid.uuid4()),
                    "brand_id": brand_id,
                    "tenant_id": tenant_id,
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

    async def update_source_content(
        self,
        brand_id: str,
        source_id: str,
        content: str,
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Re-index an existing source in place (same id, so any Shopify
        tagging on it survives) instead of upload_text()'s create-a-new-id
        path. Used by the Knowledge Base editor's Save action - also doubles
        as "retry" for a source that failed with no chunks: the merchant
        pastes the content back in and Save re-embeds it under the same id.
        Marks the source merchant_edited so a future Shopify resync
        (_clear_previous_import) knows to leave it alone instead of
        silently wiping the edit."""
        try:
            sources = supabase_select("knowledge_base_sources", {
                "id": f"eq.{source_id}",
                "brand_id": f"eq.{brand_id}",
            })
            if not sources:
                return {"success": False, "error": "Source not found"}
            source = sources[0]

            tenant_id = source.get("tenant_id") or self._get_tenant_id(brand_id)
            if not tenant_id:
                return {"success": False, "error": "Could not resolve tenant for this brand"}

            display_name = name or source.get("name")
            chunks = self._chunk_text(content, display_name)
            if not chunks:
                return {"success": False, "error": "No content to process"}

            supabase_update("knowledge_base_sources", {"id": f"eq.{source_id}"}, {"status": "processing"})
            # Old chunks are replaced wholesale by the edited content - never
            # left mixed with the new version.
            supabase_delete("rag_chunks", {"source_id": f"eq.{source_id}"})

            source_metadata = {**(source.get("metadata") or {}), "merchant_edited": True}
            successful_chunks = 0
            total_tokens = 0
            for chunk in chunks:
                embedding = await self._get_embedding(chunk["content"])
                if not embedding:
                    logger.warning(f"[KB] Failed to embed chunk {chunk['chunk_index']} while updating {source_id}")
                    continue

                chunk_record = {
                    "id": str(uuid.uuid4()),
                    "brand_id": brand_id,
                    "tenant_id": tenant_id,
                    "source_id": source_id,
                    "content": chunk["content"],
                    "embedding": embedding,
                    "source_name": chunk["source_name"],
                    "chunk_index": chunk["chunk_index"],
                    "token_count": len(chunk["content"].split()),
                    "metadata": source_metadata,
                }
                supabase_insert("rag_chunks", chunk_record)
                successful_chunks += 1
                total_tokens += chunk_record["token_count"]

            update_fields: Dict[str, Any] = {
                "chunk_count": successful_chunks,
                "total_tokens": total_tokens,
                "metadata": source_metadata,
            }
            if name and name != source.get("name"):
                update_fields["name"] = name

            if successful_chunks == 0:
                update_fields["status"] = "failed"
                update_fields["error_message"] = "Failed to generate embeddings"
                supabase_update("knowledge_base_sources", {"id": f"eq.{source_id}"}, update_fields)
                return {"success": False, "error": "Failed to generate embeddings"}

            update_fields["status"] = "completed"
            update_fields["error_message"] = None
            supabase_update("knowledge_base_sources", {"id": f"eq.{source_id}"}, update_fields)
            logger.info(f"[KB] Updated source {source_id}: {successful_chunks} chunks")
            return {
                "success": True,
                "source_id": source_id,
                "chunk_count": successful_chunks,
                "total_tokens": total_tokens,
            }

        except Exception as e:
            logger.error(f"[KB] Update error: {e}")
            return {"success": False, "error": str(e)}

    async def get_source_content(self, brand_id: str, source_id: str) -> Optional[str]:
        """Reconstructs a source's readable text from its stored chunks
        (ordered by chunk_index) - the only place the text lives, since
        knowledge_base_sources itself never stored raw content. Adjacent
        chunks overlap slightly by design (see _chunk_text), so this is a
        readable approximation of the original for viewing/editing, not a
        byte-exact reproduction."""
        chunks = supabase_select("rag_chunks", {
            "source_id": f"eq.{source_id}",
            "brand_id": f"eq.{brand_id}",
            "select": "content,chunk_index",
            "order": "chunk_index.asc",
        })
        if not chunks:
            return None
        return "\n\n".join(c.get("content", "") for c in chunks)

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

    @staticmethod
    def _format_context(results: List[Dict[str, Any]]) -> str:
        context_parts = []
        for res in results:
            source = res.get("source_name", "Knowledge Base")
            content = res.get("content", "")
            similarity = res.get("similarity", 0)
            logger.debug(f"[KB] Match: {source} (similarity: {similarity:.2f})")
            context_parts.append(f"[{source}]:\n{content}")
        return "\n\n---\n\n".join(context_parts)

    async def _fts_search(self, brand_id: str, query: str, top_k: int) -> Optional[List[Dict[str, Any]]]:
        """Postgres full-text search over rag_chunks.content (match_brand_rag_chunks_fts,
        migrations/057) - the fallback retrieval path used only when semantic
        (embedding) search is unavailable: embedding generation failed, or the
        vector RPC itself errored. This is NOT a second knowledge base - same
        rag_chunks rows, same brand scoping, same _is_low_value_policy_chunk
        filtering as the vector path, just a different way of finding relevant
        rows when an embedding can't be generated.

        Returns None (not []) on an actual failure, so the caller can tell
        "search ran and matched nothing" apart from "the search itself
        couldn't run" - same distinction already made for the vector path.
        """
        try:
            raw_results = supabase_rpc("match_brand_rag_chunks_fts", {
                "p_brand_id": brand_id,
                "query_text": query,
                "match_count": max(top_k * 10, 100),
            })
        except Exception as e:
            logger.error(f"[KB] match_brand_rag_chunks_fts RPC failed for brand {brand_id}: {e}")
            return None
        if raw_results is None:
            return None
        return [r for r in raw_results if not _is_low_value_policy_chunk(r)][:top_k]

    async def _fts_fallback(self, brand_id: str, query: str, top_k: int):
        """Formats _fts_search()'s result into the same (context, status)
        shape get_brand_context_with_status returns for the vector path."""
        results = await self._fts_search(brand_id, query, top_k)
        if results is None:
            return "", "unavailable"
        if not results:
            logger.info(f"[KB] Full-text fallback found no matching context for brand {brand_id}")
            return "", "no_match"
        logger.info(f"[KB] Full-text fallback matched {len(results)} chunk(s) for brand {brand_id}")
        return self._format_context(results), "ok"

    async def get_brand_context_with_status(
        self,
        brand_id: str,
        query: str,
        top_k: int = 5
    ):
        """
        Same retrieval as get_brand_context(), but also reports *why* the
        context came back empty - a caller that only sees "" cannot tell
        "we checked and there's genuinely no relevant policy" apart from
        "we couldn't check at all" (a failed embedding call or a
        rate-limited/timed-out RPC), and collapsing those risks telling a
        customer the store has no return policy when retrieval simply
        failed.

        Semantic (vector/embedding) search is the primary path - it ranks by
        actual meaning, not just shared keywords. Whenever the embedding
        provider is unavailable (rate-limited, timed out, exhausted quota) or
        the vector RPC itself errors, this transparently falls back to
        Postgres full-text search (_fts_fallback) over the same rag_chunks
        table, so ordinary merchant KB/policy questions keep working without
        ever depending on the embedding provider. A vector search that runs
        successfully and finds nothing is a genuine no-match and does NOT
        fall back - embeddings worked, they just found no relevant content.

        Returns (context: str, status: str) where status is one of:
          "ok"          - relevant chunks found (semantic or full-text), context is non-empty
          "no_match"    - retrieval ran fine, nothing relevant was found
          "unavailable" - neither the embedding+vector path nor the full-text fallback could run
        """
        try:
            # Generate query embedding. create_embedding() already returns
            # None (never raises) when no Mistral key is configured or every
            # one fails, so there's no separate pre-check needed here.
            embedding = await self._get_embedding(query)
            if not embedding:
                logger.warning(f"[KB] Embedding unavailable for brand {brand_id} - falling back to Postgres full-text search")
                return await self._fts_fallback(brand_id, query, top_k)

            # Search brand's knowledge base using RPC function. Over-fetch a
            # wider candidate pool than top_k, then drop low-value policy
            # chunks (see _is_low_value_policy_chunk) before truncating to
            # top_k - filtering only the requested top_k itself would just
            # shrink the result set on a store whose Privacy Policy already
            # fills every slot, not surface the relevant chunks underneath it.
            try:
                raw_results = supabase_rpc("match_brand_rag_chunks", {
                    "p_brand_id": brand_id,
                    "query_embedding": embedding,
                    "match_threshold": 0.5,
                    "match_count": max(top_k * 10, 100)
                })
            except Exception as e:
                logger.error(f"[KB] match_brand_rag_chunks RPC failed for brand {brand_id}: {e} - falling back to Postgres full-text search")
                return await self._fts_fallback(brand_id, query, top_k)

            results = [r for r in (raw_results or []) if not _is_low_value_policy_chunk(r)][:top_k]

            if not results:
                logger.info(f"[KB] No matching context for brand {brand_id}")
                return "", "no_match"

            return self._format_context(results), "ok"

        except Exception as e:
            logger.error(f"[KB] Context retrieval error: {e}")
            return "", "unavailable"

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
            Formatted context string. Backward-compatible wrapper around
            get_brand_context_with_status() - collapses "no_match" and
            "unavailable" into "" like before. Callers that need to react
            differently to a genuine retrieval failure (e.g. avoid telling a
            customer "we don't offer that" when we simply couldn't check)
            should call get_brand_context_with_status() directly instead.
        """
        context, _status = await self.get_brand_context_with_status(brand_id, query, top_k)
        return context

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
            embedding = await self._get_embedding(query)
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
