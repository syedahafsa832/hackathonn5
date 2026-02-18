from sqlalchemy import Column, String, DateTime, UUID, Text, Integer, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.sql import func
from ..services.database import Base
import uuid

class TicketFeedback(Base):
    __tablename__ = "ticket_feedback"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id = Column(PG_UUID(as_uuid=True), ForeignKey("tickets.id"), nullable=False)
    conversation_id = Column(PG_UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False)
    customer_rating = Column(Integer)  # 1-5 star rating
    was_helpful = Column(Boolean, nullable=False, default=False)
    feedback_comment = Column(Text, nullable=True)
    resolution_status = Column(String(50), nullable=False, default="resolved")  # 'resolved', 'partially_resolved', 'unresolved'
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    def __repr__(self):
        return f"<TicketFeedback(id={self.id}, ticket_id={self.ticket_id}, rating={self.customer_rating}, was_helpful={self.was_helpful})>"


class SuccessfulQAPair(Base):
    __tablename__ = "successful_qa_pairs"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    original_question = Column(Text, nullable=False)
    ai_response = Column(Text, nullable=False)
    customer_rating = Column(Integer, nullable=False)  # 1-5 rating
    ticket_id = Column(PG_UUID(as_uuid=True), ForeignKey("tickets.id"), nullable=True)
    category = Column(String(100), nullable=True)
    channel = Column(String(50), nullable=True)
    times_reused = Column(Integer, default=0)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_active = Column(Boolean, default=True)

    def __repr__(self):
        return f"<SuccessfulQAPair(id={self.id}, rating={self.customer_rating}, times_reused={self.times_reused})>"