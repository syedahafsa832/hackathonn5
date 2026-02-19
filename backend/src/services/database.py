import os
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Integer

# Database configuration
raw_db_url = os.getenv("DATABASE_URL")

# Log detection of DATABASE_URL safely
import logging
logger = logging.getLogger(__name__)

if raw_db_url:
    # Mask password for safe logging
    from urllib.parse import urlparse
    try:
        parsed = urlparse(raw_db_url)
        safe_url = f"{parsed.scheme}://{parsed.username}:****@{parsed.hostname}:{parsed.port}{parsed.path}"
        logger.info(f"DATABASE_URL detected: {safe_url}")
    except Exception:
        logger.info("DATABASE_URL detected but could not be parsed for safe logging.")
    
    # Ensure URL starts with postgresql+asyncpg://
    if raw_db_url.startswith("postgres://"):
        DATABASE_URL = raw_db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif raw_db_url.startswith("postgresql://"):
        DATABASE_URL = raw_db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    else:
        DATABASE_URL = raw_db_url
else:
    logger.warning("DATABASE_URL not found in environment, using default local development URL.")
    DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/fte_db"

# Create async engine with connection pooling
engine = create_async_engine(
    DATABASE_URL,
    pool_size=int(os.getenv("DATABASE_POOL_SIZE", "20")),
    max_overflow=int(os.getenv("DATABASE_MAX_OVERFLOW", "10")),
    pool_timeout=int(os.getenv("DATABASE_POOL_TIMEOUT", "30")),
    pool_recycle=int(os.getenv("DATABASE_POOL_RECYCLE", "3600")),
    pool_pre_ping=True,
    echo=False
)


# Create async session maker
AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Create base class for models
class Base(DeclarativeBase):
    metadata = MetaData()

from contextlib import asynccontextmanager

# Dependency to get DB session (for FastAPI)
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        yield session

# Context manager for getting a DB session (for standalone scripts/workers)
@asynccontextmanager
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        yield session
