"""
process_message()'s outermost exception handler used to return an error
dict without ever updating the ticket's own DB status - a ticket already
created (STAGE 1.8, status="processing") stayed stuck there forever if
anything later in the pipeline threw unexpectedly, since every OTHER exit
path (STAGE 9's routing update, the provider-outage branch, manual mode)
moves status off "processing" before returning, but a crash skips straight
past all of them. Left uncorrected, the dashboard's "Luna is writing..."
composer indicator (which goes false only when status leaves "processing")
would show an infinite loader for a request that had already silently died.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402


@pytest.mark.asyncio
async def test_ticket_is_escalated_when_pipeline_crashes_after_creation():
    proc = UnifiedMessageProcessor()
    message = {
        "channel": "email", "content": "Where is my order?", "customer_email": "c@example.com",
        "customer_name": "Jane", "subject": "Order question", "store_id": "brand-1",
    }
    update_calls = []

    def fake_update(table, match, data):
        if table == "tickets":
            update_calls.append((match, data))
        return {}

    with patch("src.workers.message_processor.supabase_select", return_value=[{"id": "tenant-1"}]), \
         patch("src.workers.message_processor.supabase_update", side_effect=fake_update), \
         patch("src.workers.message_processor.supabase_insert", return_value={}), \
         patch("src.workers.message_processor.supabase_service.create_ticket", new=AsyncMock(return_value={"id": "ticket-crash-1"})), \
         patch("src.workers.message_processor.supabase_service.get_system_settings", new=AsyncMock(return_value={"ai_mode": "active", "confidence_threshold": 0.65})), \
         patch("src.workers.message_processor.supabase_service.get_or_create_customer", new=AsyncMock(return_value={"id": "customer-1"})), \
         patch("src.services.plan_service.check_ai_entitlement", return_value={"allowed": True, "reason": None, "plan": "trial", "trial_expired": False}), \
         patch("src.services.plan_service.record_email_processed"), \
         patch("src.services.plan_service.can_process_ticket", return_value={"allowed": True}), \
         patch("src.services.plan_service.record_ticket_created"), \
         patch("src.services.plan_service.check_limit", return_value={"allowed": True}), \
         patch("src.workers.message_processor.customer_success_agent.generate_channel_appropriate_response",
               new=AsyncMock(side_effect=RuntimeError("boom - unexpected crash"))):
        result = await proc.process_message("email_incoming", message)

    assert result["ticket_id"] == "ticket-crash-1"
    assert result["status"] == "error"

    ticket_updates = [data for match, data in update_calls if match.get("id") == "eq.ticket-crash-1"]
    assert ticket_updates, "expected the crashed ticket's status to be updated"
    final = ticket_updates[-1]
    assert final["status"] == "escalated"
    assert "boom - unexpected crash" in final["escalation_reason"]


@pytest.mark.asyncio
async def test_crash_before_ticket_creation_does_not_error_on_missing_id():
    """If the crash happens before STAGE 1.8 ever runs (ticket creation
    itself fails), early_ticket_id is still None - the except block must
    not itself blow up trying to update a ticket that was never created."""
    proc = UnifiedMessageProcessor()
    message = {
        "channel": "email", "content": "hi", "customer_email": "c@example.com",
        "customer_name": "Jane", "subject": "x", "store_id": "brand-1",
    }

    with patch("src.workers.message_processor.supabase_select", return_value=[]), \
         patch("src.workers.message_processor.supabase_service.create_ticket",
               new=AsyncMock(side_effect=RuntimeError("ticket creation itself failed"))):
        result = await proc.process_message("email_incoming", message)

    assert result["ticket_id"] is None
    assert result["status"] == "error"
