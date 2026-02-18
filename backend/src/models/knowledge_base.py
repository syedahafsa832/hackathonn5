from sqlalchemy import Column, String, DateTime, Text, Integer, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.sql import func
from ..services.database import Base, Vector
import uuid

class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    category = Column(String(100))
    tags = Column(JSONB)  # Array of tags
    version = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
    embedding = Column(Vector(384))  # Vector embedding for similarity search

    def __repr__(self):
        return f"<KnowledgeBase(id={self.id}, title='{self.title}', category='{self.category}', active={self.is_active})>"
