"""
Knowledge Base API Routes (v2)
==============================
Per-brand knowledge base management for RAG.
"""

import io
import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File, Form
from pydantic import BaseModel, Field

from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
from src.api.routes.v2_brands import _get_owned_brand
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


class UpdateSourceRequest(BaseModel):
    content: str = Field(..., min_length=1)
    name: Optional[str] = Field(None, min_length=1, max_length=255)


# Max upload size and supported extensions for the file-upload endpoint
# below. Kept small/simple on purpose - the goal is merchant policy docs
# (return policy, FAQ, shipping info as .txt/.md/.pdf/.docx), not a general
# document management system.
_MAX_UPLOAD_FILE_BYTES = 10 * 1024 * 1024  # 10MB
_SUPPORTED_FILE_EXTENSIONS = {"txt", "md", "pdf", "docx"}


def _extract_text_from_file(filename: str, raw: bytes) -> str:
    """Extracts plain text from a supported document file. Raises ValueError
    with a merchant-facing message for anything unsupported or unreadable -
    never silently returns empty/garbage text."""
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext not in _SUPPORTED_FILE_EXTENSIONS:
        supported = ", ".join(f".{e}" for e in sorted(_SUPPORTED_FILE_EXTENSIONS))
        raise ValueError(f"Unsupported file type — supported types: {supported}")

    if ext in ("txt", "md"):
        return raw.decode("utf-8", errors="ignore")

    if ext == "pdf":
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(raw))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:
            raise ValueError(f"Could not read this PDF: {e}")

    # ext == "docx"
    from docx import Document
    try:
        doc = Document(io.BytesIO(raw))
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception as e:
        raise ValueError(f"Could not read this Word document: {e}")


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
    tenant: TenantContext = Depends(get_current_tenant)
):
    """List all knowledge base sources for a brand"""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        sources = await brand_knowledge_service.get_sources(brand_id)

        return {
            "sources": sources,
            "count": len(sources)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing KB sources: {e}")
        raise HTTPException(status_code=500, detail="Failed to list sources")


@router.get("/sources/{source_id}")
async def get_source(
    brand_id: str,
    source_id: str,
    tenant: TenantContext = Depends(get_current_tenant)
):
    """Get a specific knowledge base source"""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        sources = supabase_select("knowledge_base_sources", {
            "id": f"eq.{source_id}",
            "brand_id": f"eq.{brand_id}"
        })

        if not sources:
            raise HTTPException(status_code=404, detail="Source not found")

        source = sources[0]

        # Reconstruct the readable content from its stored chunks - this is
        # the only place the text lives (knowledge_base_sources never stored
        # raw content), so the document viewer/editor reads from here.
        chunks = supabase_select("rag_chunks", {
            "source_id": f"eq.{source_id}",
            "brand_id": f"eq.{brand_id}",
            "select": "content,chunk_index",
            "order": "chunk_index.asc",
        })
        source["actual_chunk_count"] = len(chunks) if chunks else 0
        source["content"] = "\n\n".join(c.get("content", "") for c in (chunks or []))

        return {"source": source}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting KB source: {e}")
        raise HTTPException(status_code=500, detail="Failed to get source")


@router.put("/sources/{source_id}")
async def update_source(
    brand_id: str,
    source_id: str,
    request: UpdateSourceRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Edit a knowledge base source's content and re-index it in place
    (same source_id, so Shopify tagging/ownership on it survives). This is
    also how a source that failed with no content gets "retried": the
    merchant pastes the content back in here and Save re-embeds it."""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        result = await brand_knowledge_service.update_source_content(
            brand_id=brand_id,
            source_id=source_id,
            content=request.content.strip(),
            name=request.name.strip() if request.name else None,
        )

        if not result.get("success"):
            not_found = "not found" in (result.get("error") or "").lower()
            raise HTTPException(status_code=404 if not_found else 400, detail=result.get("error"))

        return {
            "success": True,
            "source_id": result.get("source_id"),
            "chunk_count": result.get("chunk_count"),
            "total_tokens": result.get("total_tokens"),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating KB source: {e}")
        raise HTTPException(status_code=500, detail="Failed to update source")


@router.post("/upload")
async def upload_text(
    brand_id: str,
    request: UploadTextRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """
    Upload text content to the knowledge base.

    The content will be chunked, embedded, and stored for RAG retrieval.
    """
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        logger.info(f"[KB] Uploading: {request.name} for brand {brand_id}")

        result = await brand_knowledge_service.upload_text(
            brand_id=brand_id,
            name=request.name,
            content=request.content,
            user_id=tenant.tenant_id,
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


@router.post("/upload-file")
async def upload_file(
    brand_id: str,
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """
    Upload a document FILE (.txt/.md/.pdf/.docx) to the knowledge base.

    Extracts plain text from the file, then hands off to the exact same
    brand_knowledge_service.upload_text() pipeline the paste-text /upload
    endpoint above already uses for chunking, embedding, and storage -
    no second ingestion system, no new source_type.
    """
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)

        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")
        if len(raw) > _MAX_UPLOAD_FILE_BYTES:
            raise HTTPException(status_code=400, detail="File is too large (10MB limit).")

        try:
            text = _extract_text_from_file(file.filename or "", raw)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        text = text.strip()
        if len(text) < 10:
            raise HTTPException(status_code=400, detail="Couldn't extract readable text from this file.")

        doc_name = (name or file.filename or "Untitled document").strip()[:255]
        logger.info(f"[KB] Uploading file '{file.filename}' as '{doc_name}' for brand {brand_id}")

        result = await brand_knowledge_service.upload_text(
            brand_id=brand_id,
            name=doc_name,
            content=text,
            user_id=tenant.tenant_id,
            metadata={"type": "file_upload", "original_filename": file.filename},
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Failed to process this file")
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
        logger.error(f"Error uploading KB file: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload file")


@router.delete("/sources/{source_id}")
async def delete_source(
    brand_id: str,
    source_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Delete a knowledge base source and all its chunks"""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
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
    tenant: TenantContext = Depends(get_current_tenant)
):
    """Search the knowledge base"""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
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

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error searching KB: {e}")
        raise HTTPException(status_code=500, detail="Failed to search knowledge base")


@router.get("/context")
async def get_context(
    brand_id: str,
    query: str = Query(..., min_length=1),
    top_k: int = Query(5, ge=1, le=10),
    tenant: TenantContext = Depends(get_current_tenant)
):
    """Get RAG context for a query (used by AI)"""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
        rag_context = await brand_knowledge_service.get_brand_context(
            brand_id=brand_id,
            query=query,
            top_k=top_k
        )

        return {
            "context": rag_context,
            "has_context": bool(rag_context)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting KB context: {e}")
        raise HTTPException(status_code=500, detail="Failed to get context")


@router.get("/stats")
async def get_kb_stats(
    brand_id: str,
    tenant: TenantContext = Depends(get_current_tenant)
):
    """Get knowledge base statistics"""
    try:
        _get_owned_brand(brand_id, tenant.tenant_id)
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

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting KB stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get stats")
