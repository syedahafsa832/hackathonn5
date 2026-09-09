"""
Concurrency regression test for the ticket-detail-blocks-during-processing bug.

Root cause: this backend runs as a single gunicorn worker (-w 1) with one
asyncio event loop shared by the HTTP server and ticket processing. Before
the asyncio.to_thread fix, a slow synchronous Supabase call anywhere in the
processing pipeline (e.g. _log_ticket_event's supabase_insert) held the only
thread, so a concurrent GET /api/tickets/{id} literally could not run until
processing finished.

This test proves the opposite is now true: a slow blocking call inside
_log_ticket_event (patched to simulate real network latency) does NOT
prevent a concurrent ticket-detail read (supabase_service.get_ticket_by_id,
the function backing GET /api/tickets/{id}) from completing quickly.
"""
import asyncio
import os
import sys
import time
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import pytest  # noqa: E402

from src.workers.message_processor import _log_ticket_event  # noqa: E402
from src.services.supabase_service import supabase_service  # noqa: E402

SLOW_CALL_SECONDS = 1.5


def _slow_blocking_insert(table, payload):
    """Stands in for a real network call taking 1.5s (a slow Supabase write
    during ticket processing) — genuinely blocks whichever thread calls it,
    exactly like the real `requests`-based supabase_insert does."""
    time.sleep(SLOW_CALL_SECONDS)
    return {"id": "evt-1"}


def _fast_select(table, params):
    """Stands in for the real supabase_select behind GET /api/tickets/{id} —
    fast, as a real single-row lookup normally is."""
    return [{"id": "ticket-1", "status": "processing"}]


@pytest.mark.asyncio
async def test_slow_ticket_event_write_does_not_block_concurrent_ticket_read():
    with patch("src.workers.message_processor.supabase_insert", side_effect=_slow_blocking_insert), \
         patch("src.services.supabase_service.supabase_select", side_effect=_fast_select):

        slow_task = asyncio.create_task(
            _log_ticket_event("ticket-1", "brand-1", "preparing", "Preparing your answer…")
        )
        # Give the slow task a moment to actually start (enter its thread)
        # before racing the fast read against it — otherwise both might
        # start too close together to prove anything either way.
        await asyncio.sleep(0.05)

        fast_start = time.monotonic()
        ticket = await supabase_service.get_ticket_by_id("ticket-1")
        fast_elapsed = time.monotonic() - fast_start

        assert ticket == {"id": "ticket-1", "status": "processing"}
        # The whole point: the fast read must NOT have waited behind the
        # slow write. Generous bound (SLOW_CALL_SECONDS/2) well clear of
        # normal scheduling jitter, but nowhere near the 1.5s it would take
        # if the fast read were actually stuck behind the slow one.
        assert fast_elapsed < SLOW_CALL_SECONDS / 2, (
            f"ticket read took {fast_elapsed:.2f}s while a slow write was in flight — "
            f"event loop appears blocked"
        )

        # Clean up: the slow task should still be running or just finishing —
        # await it so the test doesn't leak a background task.
        await slow_task
