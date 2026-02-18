from sqlalchemy import Column, String, DateTime, UUID, Text, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.sql import func
from ..services.database import Base
import uuid

class Message(Base):
    __tablename__ = "messages"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(PG_UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False)
    channel = Column(String(50), nullable=False)  # 'whatsapp', 'web_form'
    direction = Column(String(50), nullable=False)  # 'inbound', 'outbound'
    sender_identifier = Column(String(255))
    content = Column(Text, nullable=False)
    delivery_status = Column(String(50), default="pending")  # 'sent', 'delivered', 'failed', 'pending'
    sentiment_score = Column(Numeric(3, 2))  # Between -1.0 and 1.0
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    metadata_json = Column(JSONB, name='metadata')  # Renamed from 'metadata' to avoid conflict

    def __repr__(self):
        return f"<Message(id={self.id}, conversation_id={self.conversation_id}, channel='{self.channel}', direction='{self.direction}')>"
