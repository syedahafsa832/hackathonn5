import asyncio
import sys
sys.path.insert(0, '/app')

from src.services.database import engine, Base
from src.models import customer, conversation, message, ticket, customer_identifier, knowledge_base, ticket_feedback

async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✓ Tables created successfully!")

asyncio.run(create_tables())
