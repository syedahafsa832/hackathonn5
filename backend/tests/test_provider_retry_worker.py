"""
ProviderRetryWorker.run_cycle — the polling loop (same shape as
retention_worker.py) that drives automatic recovery: reclaim any orphaned
'processing' rows, claim due rows, resume each via
message_processor.retry_pending_response(), and only reschedule/exhaust
the ones still reporting a provider outage.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.workers.provider_retry_worker import ProviderRetryWorker  # noqa: E402


@pytest.mark.asyncio
async def test_run_cycle_reclaims_stale_rows_before_claiming():
    processor = MagicMock(retry_pending_response=AsyncMock())
    with patch("src.workers.provider_retry_worker.provider_retry_service.reclaim_stale_processing") as mock_reclaim, \
         patch("src.workers.provider_retry_worker.provider_retry_service.find_and_recover_stale_tickets"), \
         patch("src.workers.provider_retry_worker.provider_retry_service.claim_due_retries", return_value=[]):
        await ProviderRetryWorker(processor).run_cycle()
    mock_reclaim.assert_called_once()


@pytest.mark.asyncio
async def test_run_cycle_recovers_stale_tickets_before_claiming():
    processor = MagicMock(retry_pending_response=AsyncMock())
    with patch("src.workers.provider_retry_worker.provider_retry_service.reclaim_stale_processing"), \
         patch("src.workers.provider_retry_worker.provider_retry_service.find_and_recover_stale_tickets") as mock_recover, \
         patch("src.workers.provider_retry_worker.provider_retry_service.claim_due_retries", return_value=[]):
        await ProviderRetryWorker(processor).run_cycle()
    mock_recover.assert_called_once()


@pytest.mark.asyncio
async def test_run_cycle_does_nothing_when_no_rows_are_due():
    processor = MagicMock(retry_pending_response=AsyncMock())
    with patch("src.workers.provider_retry_worker.provider_retry_service.reclaim_stale_processing"), \
         patch("src.workers.provider_retry_worker.provider_retry_service.find_and_recover_stale_tickets"), \
         patch("src.workers.provider_retry_worker.provider_retry_service.claim_due_retries", return_value=[]):
        await ProviderRetryWorker(processor).run_cycle()
    processor.retry_pending_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_cycle_reschedules_only_still_failing_rows():
    rows = [{"id": "row-1", "ticket_id": "t-1"}, {"id": "row-2", "ticket_id": "t-2"}]
    outcomes = [{"outcome": "sent"}, {"outcome": "retryable_failure", "error": "still rate limited"}]
    processor = MagicMock(retry_pending_response=AsyncMock(side_effect=outcomes))

    with patch("src.workers.provider_retry_worker.provider_retry_service.reclaim_stale_processing"), \
         patch("src.workers.provider_retry_worker.provider_retry_service.find_and_recover_stale_tickets"), \
         patch("src.workers.provider_retry_worker.provider_retry_service.claim_due_retries", return_value=rows), \
         patch("src.workers.provider_retry_worker.provider_retry_service.reschedule_or_exhaust") as mock_reschedule:
        await ProviderRetryWorker(processor).run_cycle()

    assert processor.retry_pending_response.await_count == 2
    mock_reschedule.assert_called_once_with(rows[1], "still rate limited")


@pytest.mark.asyncio
async def test_run_cycle_reschedules_on_unexpected_exception_instead_of_crashing_the_loop():
    """One ticket's retry blowing up (e.g. a transient DB error) must not
    take the whole worker down or silently drop the job — it gets
    rescheduled/exhausted through the same bounded path as a provider
    failure."""
    rows = [{"id": "row-1", "ticket_id": "t-1"}]
    processor = MagicMock(retry_pending_response=AsyncMock(side_effect=RuntimeError("boom")))

    with patch("src.workers.provider_retry_worker.provider_retry_service.reclaim_stale_processing"), \
         patch("src.workers.provider_retry_worker.provider_retry_service.find_and_recover_stale_tickets"), \
         patch("src.workers.provider_retry_worker.provider_retry_service.claim_due_retries", return_value=rows), \
         patch("src.workers.provider_retry_worker.provider_retry_service.reschedule_or_exhaust") as mock_reschedule:
        await ProviderRetryWorker(processor).run_cycle()  # must not raise

    mock_reschedule.assert_called_once()
    assert mock_reschedule.call_args.args[0] == rows[0]
