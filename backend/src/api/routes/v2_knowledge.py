"""
Knowledge Base API Routes (v2)
==============================
Per-brand knowledge base management for RAG.
"""

import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from src.api.middleware.auth_middleware import (
    AuthenticatedContext,
    get_current_user,
    require_admin,
    require_brand_access
)
from src.services.brand_knowledge_service import brand_knowledge_service
from src.lib.supabase_client import supabase_select

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/brands/{brand_id}/knowledge", tags=["Knowledge Base"])


# ==================== Request/Response Models ====================

class UploadTextRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=10)
    metadata: Optional[dict] = None


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(5, ge=1, le=20)


class SourceResponse(BaseModel):
    id: str
    name: str
    source_type: str
    status: str
    chunk_count: int
    total_tokens: int
    created_at: str
    error_message: Optional[str] = None


# ==================== Routes ====================

@router.get("/sources")
async def list_sources(
    brand_id: str,
    context: AuthenticatedContext = Depends(require_brand_access("brand_id"))
):
    """List all knowledge base sources for a brand"""
    try:
        sources = await brand_knowledge_service.get_sources(brand_id)

        return {
            "sources": sources,
            "count": len(sources)
        }

    except Exception as e:
        logger.error(f"Error listing KB sources: {e}")
        raise HTTPException(status_code=500, detail="Failed to list sources")


@router.get("/sources/{source_id}")
async def get_source(
    brand_id: str,
    source_id: str,
    context: AuthenticatedContext = Depends(require_brand_access("brand_id"))
):
    """Get a specific knowledge base source"""
    try:
        sources = supabase_select("knowledge_base_sources", {
            "id": f"eq.{source_id}",
            "brand_id": f"eq.{brand_id}"
        })

        if not sources:
            raise HTTPException(status_code=404, detail="Source not found")

        source = sources[0]

        # Get chunk count
        chunks = supabase_select("rag_chunks", {
            "source_id": f"eq.{source_id}",
            "select": "id"
        })
        source["actual_chunk_count"] = len(chunks) if chunks else 0

        return {"source": source}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting KB source: {e}")
        raise HTTPException(status_code=500, detail="Failed to get source")


@router.post("/upload")
async def upload_text(
    brand_id: str,
    request: UploadTextRequest,
    context: AuthenticatedContext = Depends(require_admin)
):
    """
    Upload text content to the knowledge base.

    The content will be chunked, embedded, and stored for RAG retrieval.
    """
    try:
        logger.info(f"[KB] Uploading: {request.name} for brand {brand_id}")

        result = await brand_knowledge_service.upload_text(
            brand_id=brand_id,
            name=request.name,
            content=request.content,
            user_id=context.user.user_id,
            metadata=request.metadata
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Failed to upload content")
            )

        return {
            "success": True,
            "message": f"Processed {result.get('chunk_count')} chunks",
            "source_id": result.get("source_id"),
            "chunk_count": result.get("chunk_count"),
            "total_tokens": result.get("total_tokens")
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading KB content: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload content")


@router.delete("/sources/{source_id}")
async def delete_source(
    brand_id: str,
    source_id: str,
    context: AuthenticatedContext = Depends(require_admin)
):
    """Delete a knowledge base source and all its chunks"""
    try:
        result = await brand_knowledge_service.delete_source(brand_id, source_id)

        if not result.get("success"):
            raise HTTPException(
                status_code=404 if "not found" in result.get("error", "").lower() else 400,
                detail=result.get("error")
            )

        return {
            "success": True,
            "message": "Source deleted successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting KB source: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete source")


@router.post("/search")
async def search_knowledge(
    brand_id: str,
    request: SearchRequest,
    context: AuthenticatedContext = Depends(require_brand_access("brand_id"))
):
    """Search the knowledge base"""
    try:
        results = await brand_knowledge_service.search_knowledge(
            brand_id=brand_id,
            query=request.query,
            top_k=request.top_k
        )

        return {
            "results": results,
            "count": len(results),
            "query": request.query
        }

    except Exception as e:
        logger.error(f"Error searching KB: {e}")
        raise HTTPException(status_code=500, detail="Failed to search knowledge base")


@router.get("/context")
async def get_context(
    brand_id: str,
    query: str = Query(..., min_length=1),
    top_k: int = Query(5, ge=1, le=10),
    context: AuthenticatedContext = Depends(require_brand_access("brand_id"))
):
    """Get RAG context for a query (used by AI)"""
    try:
        rag_context = await brand_knowledge_service.get_brand_context(
            brand_id=brand_id,
            query=query,
            top_k=top_k
        )

        return {
            "context": rag_context,
            "has_context": bool(rag_context)
        }

    except Exception as e:
        logger.error(f"Error getting KB context: {e}")
        raise HTTPException(status_code=500, detail="Failed to get context")


@router.get("/stats")
async def get_kb_stats(
    brand_id: str,
    context: AuthenticatedContext = Depends(require_brand_access("brand_id"))
):
    """Get knowledge base statistics"""
    try:
        sources = supabase_select("knowledge_base_sources", {
            "brand_id": f"eq.{brand_id}"
        })

        chunks = supabase_select("rag_chunks", {
            "brand_id": f"eq.{brand_id}",
            "select": "id,token_count"
        })

        total_sources = len(sources) if sources else 0
        completed_sources = len([s for s in (sources or []) if s.get("status") == "completed"])
        total_chunks = len(chunks) if chunks else 0
        total_tokens = sum(c.get("token_count", 0) for c in (chunks or []))

        return {
            "stats": {
                "total_sources": total_sources,
                "completed_sources": completed_sources,
                "failed_sources": total_sources - completed_sources,
                "total_chunks": total_chunks,
                "total_tokens": total_tokens
            }
        }

    except Exception as e:
        logger.error(f"Error getting KB stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get stats")
