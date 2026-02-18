from sqlalchemy import Column, String, DateTime, UUID, Text, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.sql import func
from ..services.database import Base
import uuid

class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(PG_UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    conversation_id = Column(PG_UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True)
    source_channel = Column(String(50), nullable=False)  # 'whatsapp', 'web_form'
    category = Column(String(100), nullable=False)
    priority = Column(String(50), nullable=False)  # 'low', 'medium', 'high', 'critical'
    status = Column(String(50), nullable=False, default="open")  # 'open', 'in_progress', 'escalated', 'resolved', 'closed'
    subject = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    assigned_agent = Column(String(255), nullable=True)
    resolution_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    escalation_reason = Column(String(100), nullable=True)

    def __repr__(self):
        return f"<Ticket(id={self.id}, customer_id={self.customer_id}, status='{self.status}', priority='{self.priority}')>"
