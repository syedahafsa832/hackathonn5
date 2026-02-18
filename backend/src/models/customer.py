from sqlalchemy import Column, String, DateTime, Text, UUID
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.sql import func
from ..services.database import Base
import uuid

class Customer(Base):
    __tablename__ = "customers"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, unique=True)
    phone = Column(String(50), nullable=True)
    name = Column(String(255), nullable=False)
    company = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
    metadata_json = Column(JSONB, name='metadata')  # Renamed from 'metadata' to avoid conflict

    def __repr__(self):
        return f"<Customer(id={self.id}, email='{self.email}', name='{self.name}')>"
