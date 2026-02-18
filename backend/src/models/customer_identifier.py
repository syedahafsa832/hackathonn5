from sqlalchemy import Column, String, DateTime, UUID, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.sql import func
from ..services.database import Base
import uuid

class CustomerIdentifier(Base):
    __tablename__ = "customer_identifiers"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(PG_UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    identifier_type = Column(String(50), nullable=False)  # 'email', 'phone', 'external_id'
    identifier_value = Column(String(255), nullable=False)
    is_primary = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<CustomerIdentifier(id={self.id}, type='{self.identifier_type}', value='{self.identifier_value}')>"
