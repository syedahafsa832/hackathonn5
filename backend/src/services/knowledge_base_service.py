from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.dialects.postgresql import insert
from typing import List, Optional, Dict, Any
import uuid
import numpy as np
import logging

from ..models.knowledge_base import KnowledgeBase
from ..models.customer import Customer

logger = logging.getLogger(__name__)

class KnowledgeBaseService:
    def __init__(self):
        # Load a small, efficient local model for embeddings
        # all-MiniLM-L6-v2 is 384 dimensions, fast and accurate
        self._model = None
        self.model_name = 'all-MiniLM-L6-v2'

    @property
    def model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                logger.info(f"Loading SentenceTransformer model: {self.model_name}")
                self._model = SentenceTransformer(self.model_name)
            except Exception as e:
                logger.error(f"Failed to load SentenceTransformer model: {str(e)}")
                # Return a dummy model or handle failure gracefully in methods
                return None
        return self._model

    async def create_embedding(self, text: str) -> List[float]:
        """
        Create an embedding for the given text using local SentenceTransformer
        """
        try:
            if self.model is None:
                logger.warning("Model not available, using fallback zero embedding")
                return [0.0] * 384
            # Running embedding in a separate thread if needed, 
            # but for this scale sync call is fine or use run_in_executor
            embedding = self.model.encode(text)
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Error creating embedding: {str(e)}")
            return [0.0] * 384

    async def search_similar(
        self,
        db: AsyncSession,
        query: str,
        top_k: int = 3,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[KnowledgeBase]:
        """
        Search for similar articles in the knowledge base using vector similarity
        """
        # Create embedding for the query
        query_embedding = await self.create_embedding(query)

        # Build the query with vector similarity
        # Simplified search without pgvector
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.is_active == True
        ).limit(top_k)

        # Apply filters if provided
        if filters:
            for key, value in filters.items():
                if hasattr(KnowledgeBase, key):
                    stmt = stmt.where(getattr(KnowledgeBase, key) == value)

        result = await db.execute(stmt)
        articles = result.scalars().all()

        return articles

    async def create_article(
        self,
        db: AsyncSession,
        title: str,
        content: str,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> KnowledgeBase:
        """
        Create a new knowledge base article with embedding
        """
        # Create embedding for the content
        embedding = await self.create_embedding(content)

        article = KnowledgeBase(
            title=title,
            content=content,
            category=category,
            tags=tags or [],
            embedding=embedding
        )

        db.add(article)
        await db.flush()

        return article

    async def update_article(
        self,
        db: AsyncSession,
        article_id: uuid.UUID,
        title: Optional[str] = None,
        content: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        is_active: Optional[bool] = None
    ) -> KnowledgeBase:
        """
        Update an existing knowledge base article
        """
        article = await self.get_article_by_id(db, article_id)

        if not article:
            raise ValueError(f"Article with ID {article_id} not found")

        # Update fields if provided
        if title is not None:
            article.title = title
        if content is not None:
            article.content = content
            # Recreate embedding if content changed
            article.embedding = await self.create_embedding(content)
        if category is not None:
            article.category = category
        if tags is not None:
            article.tags = tags
        if is_active is not None:
            article.is_active = is_active

        await db.flush()

        return article

    async def get_article_by_id(
        self,
        db: AsyncSession,
        article_id: uuid.UUID
    ) -> Optional[KnowledgeBase]:
        """
        Retrieve a knowledge base article by its ID
        """
        result = await db.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == article_id)
        )
        return result.scalar_one_or_none()

    async def get_articles_by_category(
        self,
        db: AsyncSession,
        category: str,
        active_only: bool = True
    ) -> List[KnowledgeBase]:
        """
        Retrieve all articles in a specific category
        """
        stmt = select(KnowledgeBase).where(KnowledgeBase.category == category)

        if active_only:
            stmt = stmt.where(KnowledgeBase.is_active == True)

        result = await db.execute(stmt)
        return result.scalars().all()

    async def delete_article(
        self,
        db: AsyncSession,
        article_id: uuid.UUID
    ) -> bool:
        """
        Delete a knowledge base article
        """
        article = await self.get_article_by_id(db, article_id)

        if not article:
            return False

        await db.delete(article)
        await db.flush()

        return True

    async def seed_knowledge_base(
        self,
        db: AsyncSession,
        articles_data: List[Dict[str, Any]]
    ) -> int:
        """
        Seed the knowledge base with initial articles
        """
        created_count = 0

        for article_data in articles_data:
            try:
                # Check if article with this title already exists
                existing_stmt = select(KnowledgeBase).where(
                    KnowledgeBase.title == article_data['title']
                )
                existing_result = await db.execute(existing_stmt)
                existing_article = existing_result.scalar_one_or_none()

                if not existing_article:
                    await self.create_article(
                        db=db,
                        title=article_data['title'],
                        content=article_data['content'],
                        category=article_data.get('category'),
                        tags=article_data.get('tags', [])
                    )
                    created_count += 1
            except Exception as e:
                logger.warning(f"Failed to create article '{article_data['title']}': {str(e)}")
                continue

        return created_count


# Global instance
knowledge_base_service = KnowledgeBaseService()
