#!/usr/bin/env python3
"""
Email Polling Service for Customer Success AI Agent

This service polls Gmail for new emails and forwards them to the message processor
via Kafka for AI processing.
"""

import asyncio
import logging
import os
from typing import Dict, Any, List
from datetime import datetime, timezone
import uuid

from src.services.kafka_client import kafka_client_service
from production.channels.gmail_handler import gmail_handler

logger = logging.getLogger(__name__)


class EmailPoller:
    """Service to poll Gmail for new emails and forward them for processing."""

    def __init__(self, poll_interval: int = 30):
        """
        Initialize the email poller.

        Args:
            poll_interval: Interval in seconds between email checks (default 30 seconds)
        """
        self.poll_interval = poll_interval
        self.running = False
        self.kafka_client = kafka_client_service

    async def start(self):
        """Start the email polling service."""
        logger.info(f"Starting Email Poller with {self.poll_interval}s interval...")
        self.running = True

        logger.info("Email Poller started successfully")

        # Start the main polling loop
        await self._polling_loop()

    async def stop(self):
        """Stop the email polling service."""
        logger.info("Stopping Email Poller...")
        self.running = False
        logger.info("Email Poller stopped")

    async def _polling_loop(self):
        """Main polling loop that checks for new emails."""
        logger.info("Entering email polling loop...")

        while self.running:
            try:
                # Process new emails
                await self._check_and_process_new_emails()

                # Wait for the specified interval before next check
                await asyncio.sleep(self.poll_interval)

            except Exception as e:
                logger.error(f"Error in email polling loop: {str(e)}")
                # Wait before retrying to avoid rapid error cycles
                await asyncio.sleep(min(self.poll_interval, 60))

    async def _check_and_process_new_emails(self):
        """Check for new emails and process them."""
        try:
            logger.info("Checking for new emails...")

            # Process new emails using the Gmail handler
            result = await gmail_handler.process_new_emails()

            if result['count'] > 0:
                logger.info(f"Found {result['count']} new emails to process")

                for email in result['emails']:
                    # Skip emails from our own support address to avoid infinite loops
                    sender_email = email['sender_email'].lower()
                    support_email = os.getenv("SUPPORT_EMAIL_ADDRESS", "").lower()

                    if sender_email == support_email:
                        logger.info(f"Skipping email from support address {sender_email} to avoid feedback loop")
                        continue

                    # Skip if the email contains our own signature (avoid processing our responses)
                    email_body_lower = email['body'].lower()
                    if "customer success ai agent" in email_body_lower or "automated response" in email_body_lower:
                        logger.info(f"Skipping our own response email from {sender_email}")
                        continue

                    # Format the email data for processing
                    email_for_processing = {
                        "ticket_id": str(uuid.uuid4()),  # Generate a new ticket ID
                        "customer_id": None,  # Will be resolved by the processor
                        "conversation_id": None,  # Will be created by the processor
                        "channel": "email",
                        "content": email['body'],
                        "customer_email": email['sender_email'],
                        "customer_name": email['sender_name'],
                        "subject": email['subject'],
                        "action": "created",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }

                    # Publish to Kafka for processing
                    await self.kafka_client.send_to_topic("tickets_incoming", email_for_processing)

                    logger.info(f"Forwarded email from {email['sender_email']} to message processor")
            else:
                logger.info("No new emails found")

        except Exception as e:
            logger.error(f"Error processing new emails: {str(e)}")


async def main():
    """Main function to run the email poller."""
    poller = EmailPoller(poll_interval=30)  # Poll every 30 seconds

    try:
        await poller.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        await poller.stop()


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Run the poller
    asyncio.run(main())
