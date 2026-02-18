#!/usr/bin/env python3
"""
Database initialization script for Customer Success AI Agent
"""

import asyncio
import os
import sys
import logging
from sqlalchemy import text

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from src.services.database import engine, Base
from src.models.customer import Customer
from src.models.conversation import Conversation
from src.models.message import Message
from src.models.ticket import Ticket
from src.models.customer_identifier import CustomerIdentifier
from src.models.knowledge_base import KnowledgeBaseArticle
from src.models.ticket_feedback import TicketFeedback, SuccessfulQAPair

async def init_db():
    """Initialize the database tables."""
    print("Initializing database tables...")
    logging.basicConfig(level=logging.INFO)

    try:
        # Create all tables
        async with engine.begin() as conn:
            # Create tables based on models
            await conn.run_sync(Base.metadata.create_all)

        print("Database tables created successfully!")

        # Verify tables were created
        async with engine.begin() as conn:
            # Check if customers table exists
            result = await conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = 'customers'
                );
            """))
            exists = result.scalar()
            if exists:
                print("✓ Customers table created successfully")
            else:
                print("✗ Customers table creation failed")

    except Exception as e:
        print(f"Error initializing database: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(init_db())