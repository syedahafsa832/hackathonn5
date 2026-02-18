from sqlalchemy import Column, String, DateTime, UUID, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.sql import func
from ..services.database import Base
import uuid

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(PG_UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    initial_channel = Column(String(50), nullable=False)  # 'whatsapp', 'web_form'
    status = Column(String(50), nullable=False, default="open")  # 'open', 'closed', 'escalated', 'pending'
    sentiment_score = Column(Numeric(3, 2))  # Between -1.0 and 1.0
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
    metadata_json = Column(JSONB, name='metadata')  # Renamed from 'metadata' to avoid conflict

    def __repr__(self):
        return f"<Conversation(id={self.id}, customer_id={self.customer_id}, channel='{self.initial_channel}', status='{self.status}')>"
