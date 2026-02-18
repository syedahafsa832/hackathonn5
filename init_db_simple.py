#!/usr/bin/env python3
"""
Simplified database initialization script for Customer Success AI Agent
"""

import asyncio
import os
import sys
import logging
from sqlalchemy import text

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add backend to path
sys.path.insert(0, '/app')

from src.services.database import engine, Base

async def init_db():
    """Initialize the database tables."""
    logger.info("Initializing database tables...")

    try:
        # Import all models to register them with Base
        from src.models import customer, conversation, message, ticket, customer_identifier
        try:
            from src.models import knowledge_base
            logger.info("✓ Knowledge base model imported")
        except ImportError as e:
            logger.warning(f"Could not import knowledge_base model: {e}")
        
        # Create all tables
        async with engine.begin() as conn:
            # Create tables based on models
            await conn.run_sync(Base.metadata.create_all)

        logger.info("✓ Database tables created successfully!")

        # Verify tables were created
        async with engine.begin() as conn:
            # List all tables
            result = await conn.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
                ORDER BY table_name;
            """))
            tables = result.fetchall()
            
            logger.info(f"\n{'='*50}")
            logger.info("Created tables:")
            for table in tables:
                logger.info(f"  ✓ {table[0]}")
            logger.info(f"{'='*50}\n")

    except Exception as e:
        logger.error(f"✗ Error initializing database: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(init_db())
