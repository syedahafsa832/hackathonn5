#!/usr/bin/env python3
"""
Database initialization script for Customer Success AI Agent
"""

import asyncio
import os
import sys
import logging
from sqlalchemy import text

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ensure /app is in the path
sys.path.insert(0, '/app')

from src.services.database import engine, Base

async def init_db():
    """Initialize the database tables."""
    logger.info("="*60)
    logger.info("Starting database initialization...")
    logger.info("="*60)

    try:
        # Import all models to register them with Base.metadata
        logger.info("Importing models...")
        from src.models import customer, conversation, message, ticket, customer_identifier
        
        # Try to import knowledge_base if it exists
        try:
            from src.models import knowledge_base
            logger.info("✓ All models imported successfully")
        except ImportError as e:
            logger.warning(f"Note: knowledge_base model not available: {e}")
        
        # Create all tables
        logger.info("\nCreating database tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("✓ Database schema created successfully!")

        # Verify tables were created
        logger.info("\nVerifying created tables...")
        async with engine.begin() as conn:
            result = await conn.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
                ORDER BY table_name;
            """))
            tables = result.fetchall()
            
            if tables:
                logger.info(f"\n{'='*60}")
                logger.info(f"Successfully created {len(tables)} tables:")
                logger.info(f"{'='*60}")
                for table in tables:
                    logger.info(f"  ✓ {table[0]}")
                logger.info(f"{'='*60}\n")
            else:
                logger.error("✗ No tables were created!")
                sys.exit(1)

        logger.info("Database initialization completed successfully! 🎉\n")

    except Exception as e:
        logger.error(f"\n{'='*60}")
        logger.error(f"✗ Error initializing database: {e}")
        logger.error(f"{'='*60}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(init_db())