"""
Provider-outage retry worker
==============================
Periodically claims due rows from ai_response_retries (see
migrations/058_ai_response_retries.sql and provider_retry_service.py) and
resumes AI processing for each one via message_processor's
retry_pending_response(). Same lightweight polling-loop shape as
retention_worker.py — no new dependency (no Celery/Redis/APScheduler);
state lives in the DB, not in this process, so a retry survives a restart
or redeploy.
"""
import asyncio
import logging

from src.services import provider_retry_service

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60


class ProviderRetryWorker:
    def __init__(self, processor, check_interval_seconds: int = POLL_INTERVAL_SECONDS):
        self.processor = processor
        self.check_interval = check_interval_seconds
        self.running = False

    async def start(self):
        logger.info("Starting Provider Retry Worker...")
        self.running = True
        while self.running:
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[ProviderRetryWorker] Error in retry cycle: {e}", exc_info=True)
            try:
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break

    async def stop(self):
        self.running = False

    async def run_cycle(self):
        due = provider_retry_service.claim_due_retries()
        if not due:
            return
        logger.info(f"[ProviderRetryWorker] Processing {len(due)} due retry job(s)")
        for row in due:
            try:
                result = await self.processor.retry_pending_response(row)
                outcome = result.get("outcome")
                if outcome == "retryable_failure":
                    provider_retry_service.reschedule_or_exhaust(row, result.get("error", ""))
                else:
                    logger.info(f"[ProviderRetryWorker] ticket={row.get('ticket_id')} outcome={outcome}")
            except Exception as e:
                logger.error(f"[ProviderRetryWorker] retry_pending_response failed for ticket={row.get('ticket_id')}: {e}", exc_info=True)
                provider_retry_service.reschedule_or_exhaust(row, str(e))
