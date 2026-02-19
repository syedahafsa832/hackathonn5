#!/usr/bin/env python3
"""
Email Polling Service for Customer Success AI Agent
"""

import asyncio
import logging
import os
from typing import Dict, Any, List
from datetime import datetime, timezone
import uuid

logger = logging.getLogger(__name__)
from production.channels.gmail_handler import gmail_handler

class EmailPoller:
    """Service to poll Gmail for new emails and forward them for processing."""

    def __init__(self, poll_interval: int = 30, processor=None):
        self.poll_interval = poll_interval
        self.running = False
        self.processor = processor

    async def start(self):
        logger.info(f"Starting Email Poller with {self.poll_interval}s interval...")
        self.running = True
        await self._polling_loop()

    async def stop(self):
        self.running = False
        logger.info("Email Poller stopped")

    async def _polling_loop(self):
        while self.running:
            try:
                await self._check_and_process_new_emails()
                await asyncio.sleep(self.poll_interval)
            except Exception as e:
                logger.error(f"Error in email polling loop: {str(e)}")
                await asyncio.sleep(min(self.poll_interval, 60))

    async def _check_and_process_new_emails(self):
        try:
            result = await gmail_handler.process_new_emails()
            if result['count'] > 0:
                for email in result['emails']:
                    sender_email = email['sender_email'].lower()
                    support_email = os.getenv("SUPPORT_EMAIL_ADDRESS", "").lower()

                    if sender_email == support_email: continue
                    
                    email_body_lower = email['body'].lower()
                    if "customer success ai agent" in email_body_lower: continue

                    email_for_processing = {
                        "channel": "email",
                        "content": email['body'],
                        "customer_email": email['sender_email'],
                        "customer_name": email['sender_name'],
                        "subject": email['subject'],
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }

                    if self.processor:
                        await self.processor.process_message("email_incoming", email_for_processing)
        except Exception as e:
            logger.error(f"Error processing new emails: {str(e)}")
