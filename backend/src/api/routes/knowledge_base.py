from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uuid
import time

from src.services.database import get_db
from src.models.knowledge_base import KnowledgeBase
from src.services.knowledge_base_service import knowledge_base_service

router = APIRouter()

# Pydantic models for request/response validation
class KnowledgeBaseSearchRequest(BaseModel):
    query: str
    top_k: int = 3
    filters: Optional[Dict[str, Any]] = None

class KnowledgeBaseArticle(BaseModel):
    id: str
    title: str
    content: str
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    similarity_score: float

class KnowledgeBaseSearchResponse(BaseModel):
    results: List[KnowledgeBaseArticle]
    query: str
    search_time_ms: float

class KnowledgeBaseArticleRequest(BaseModel):
    title: str
    content: str
    category: Optional[str] = None
    tags: Optional[List[str]] = None

@router.post("/search")
async def search_knowledge_base(
    request: KnowledgeBaseSearchRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Search the knowledge base using vector similarity
    """
    try:
        start_time = time.time()

        # Validate input
        if not request.query or len(request.query.strip()) == 0:
            raise HTTPException(
                status_code=400,
                detail="Query cannot be empty"
            )

        if request.top_k < 1 or request.top_k > 10:
            raise HTTPException(
                status_code=400,
                detail="top_k must be between 1 and 10"
            )

        # Perform similarity search
        articles = await knowledge_base_service.search_similar(
            db=db,
            query=request.query,
            top_k=request.top_k,
            filters=request.filters
        )

        # Convert to response format
        results = []
        for article in articles:
            # For now, we'll use a simple similarity calculation
            # In a real implementation, this would come from the vector distance
            similarity_score = 1.0  # Placeholder - actual implementation would calculate from vector distance

            results.append(
                KnowledgeBaseArticle(
                    id=str(article.id),
                    title=article.title,
                    content=article.content[:500] + "..." if len(article.content) > 500 else article.content,
                    category=article.category,
                    tags=article.tags,
                    similarity_score=similarity_score
                )
            )

        search_time_ms = (time.time() - start_time) * 1000

        return KnowledgeBaseSearchResponse(
            results=results,
            query=request.query,
            search_time_ms=search_time_ms
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while searching knowledge base: {str(e)}"
        )


@router.get("/{article_id}")
async def get_knowledge_base_article(
    article_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get a specific knowledge base article by ID
    """
    try:
        # Validate UUID format
        article_uuid = uuid.UUID(article_id)

        article = await knowledge_base_service.get_article_by_id(db, article_uuid)

        if not article:
            raise HTTPException(
                status_code=404,
                detail=f"Knowledge base article with ID {article_id} not found"
            )

        if not article.is_active:
            raise HTTPException(
                status_code=404,
                detail=f"Knowledge base article with ID {article_id} is not active"
            )

        return {
            "id": str(article.id),
            "title": article.title,
            "content": article.content,
            "category": article.category,
            "tags": article.tags,
            "version": article.version,
            "is_active": article.is_active,
            "created_at": article.created_at.isoformat() if article.created_at else "",
            "updated_at": article.updated_at.isoformat() if article.updated_at else ""
        }

    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid article ID format. Must be a valid UUID."
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while fetching article: {str(e)}"
        )


@router.post("/articles")
async def create_knowledge_base_article(
    article_data: KnowledgeBaseArticleRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new knowledge base article
    """
    try:
        # Validate input
        if not article_data.title or len(article_data.title.strip()) == 0:
            raise HTTPException(
                status_code=400,
                detail="Article title cannot be empty"
            )

        if not article_data.content or len(article_data.content.strip()) == 0:
            raise HTTPException(
                status_code=400,
                detail="Article content cannot be empty"
            )

        # Create the article
        article = await knowledge_base_service.create_article(
            db=db,
            title=article_data.title,
            content=article_data.content,
            category=article_data.category,
            tags=article_data.tags
        )

        return {
            "id": str(article.id),
            "title": article.title,
            "message": "Article created successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while creating article: {str(e)}"
        )


@router.get("/categories/{category}")
async def get_knowledge_base_articles_by_category(
    category: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get all articles in a specific category
    """
    try:
        articles = await knowledge_base_service.get_articles_by_category(
            db=db,
            category=category
        )

        return {
            "category": category,
            "articles": [
                {
                    "id": str(article.id),
                    "title": article.title,
                    "category": article.category,
                    "tags": article.tags,
                    "created_at": article.created_at.isoformat() if article.created_at else "",
                    "updated_at": article.updated_at.isoformat() if article.updated_at else ""
                }
                for article in articles
            ]
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while fetching articles: {str(e)}"
        )
