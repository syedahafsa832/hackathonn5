#!/usr/bin/env python3
"""
Learning Worker for Customer Success AI Agent

This service periodically processes successful tickets to extract and learn from
successful Q&A pairs, improving the AI's future responses.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any
import uuid

from src.services.database import db_session
from src.services.ticket_feedback_service import process_successful_tickets, search_successful_qa_pairs
from src.services.message_service import get_messages_by_conversation
# from src.services.ticket_service import get_tickets_by_status

logger = logging.getLogger(__name__)


class LearningWorker:
    """Process successful tickets to learn from them."""

    def __init__(self, learning_interval: int = 3600):  # Run every hour
        """
        Initialize the learning worker.

        Args:
            learning_interval: Interval in seconds between learning cycles (default 1 hour)
        """
        self.learning_interval = learning_interval
        self.running = False

    async def start(self):
        """Start the learning worker."""
        logger.info(f"Starting Learning Worker with {self.learning_interval}s interval...")
        self.running = True

        logger.info("Learning Worker started successfully")

        # Start the main learning loop
        await self._learning_loop()

    async def stop(self):
        """Stop the learning worker."""
        logger.info("Stopping Learning Worker...")
        self.running = False
        logger.info("Learning Worker stopped")

    async def _learning_loop(self):
        """Main learning loop that periodically processes successful tickets."""
        logger.info("Entering learning loop...")

        while self.running:
            try:
                # Process successful tickets
                await self._process_successful_tickets()

                # Wait for the specified interval before next cycle
                await asyncio.sleep(self.learning_interval)

            except Exception as e:
                logger.error(f"Error in learning loop: {str(e)}")
                # Wait before retrying to avoid rapid error cycles
                await asyncio.sleep(min(self.learning_interval, 60))

    async def _process_successful_tickets(self):
        """Process resolved tickets with high ratings to extract learning."""
        try:
            logger.info("Processing successful tickets for learning...")

            async with db_session() as db:
                # Process tickets that were resolved with high ratings
                processed_count = await process_successful_tickets(db, limit=50)

                if processed_count > 0:
                    logger.info(f"Processed {processed_count} successful tickets for learning")
                else:
                    logger.info("No new successful tickets to process")

        except Exception as e:
            logger.error(f"Error processing successful tickets: {str(e)}")


async def main():
    """Main function to run the learning worker."""
    worker = LearningWorker(learning_interval=3600)  # Run every hour

    try:
        await worker.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        await worker.stop()


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Run the worker
    asyncio.run(main())