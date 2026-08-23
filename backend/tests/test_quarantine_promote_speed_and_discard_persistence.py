"""
Two quarantine bugs:

1. Promotion was slow - POST /quarantine/{id}/promote awaited the full
   message_processor pipeline (AI reply generation + Gmail send, several
   seconds) before returning, even though the dashboard's QuarantineQueue.jsx
   never reads the response's ticket_id (it just removes the item locally on
   success) and process_message's own STAGE 1.8 already creates the ticket
   almost immediately, well before the slow AI/email work. Fix: the endpoint
   claims the record and hands the AI/email work to a background task
   (asyncio.create_task, same fire-and-forget pattern already used for
   Shopify import) instead of blocking the response on it.

2. A discarded quarantine item could come back - email_poller.py only
   deduped against tickets.gmail_message_id, so a discarded item (Gmail's
   `after:` search re-surfaces the same raw message all day) got
   re-classified from scratch on every poll, and a different/nondeterministic
   classifier outcome could let it through to a real ticket, silently
   overriding the merchant's decision. Fix: the poller now also checks
   email_quarantine for a 'discarded' record on this gmail_message_id and
   skips it before even running the filter/guardian evaluation.
"""
import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402
from src.api.routes.v2_quarantine import router as quarantine_router, _run_promotion  # noqa: E402
from src.channels.email_poller import EmailPoller  # noqa: E402

TENANT_ID = "tenant-1"
BRAND_ID = "brand-1"
QID = "quar-1"


def _app():
    app = FastAPI()
    app.include_router(quarantine_router, prefix="/api/v1")
    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(tenant_id=TENANT_ID, email="agent@example.com")
    return app


def _quarantine_row(**overrides):
    row = {
        "id": QID, "brand_id": BRAND_ID, "status": "pending",
        "sender_email": "customer@example.com", "subject": "Where is my order?",
        "body_preview": "Hey, where's my order?", "thread_id": "t1", "gmail_message_id": "msg-1",
    }
    row.update(overrides)
    return row


# ── 1. Promotion no longer blocks on the AI/email pipeline ─────────────────

def test_promote_returns_without_waiting_for_ai_pipeline():
    """The response must come back long before a slow (here: sleeping)
    background pipeline finishes - proves the endpoint schedules the work
    instead of awaiting it in-line."""
    app = _app()
    client = TestClient(app)

    async def slow_run_promotion(*args, **kwargs):
        await asyncio.sleep(3)

    def fake_select(table, params=None):
        if table == "brands":
            return [{"id": BRAND_ID, "tenant_id": TENANT_ID, "is_active": True, "gmail_connected": True}]
        if table == "email_quarantine":
            return [_quarantine_row()]
        return []

    with patch("src.api.routes.v2_quarantine.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_quarantine.supabase_update", return_value=[{"id": QID}]) as mock_update, \
         patch("src.api.routes.v2_quarantine._run_promotion", new=slow_run_promotion):
        start = time.monotonic()
        resp = client.post(f"/api/v1/quarantine/{QID}/promote")
        elapsed = time.monotonic() - start

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert elapsed < 1.5  # well under the 3s background delay
    # The record was still claimed synchronously before responding.
    claim_call = next(c for c in mock_update.call_args_list if c.args[2].get("status") == "promoted")
    assert claim_call is not None


def test_promote_payload_includes_gmail_message_id_for_dedup():
    """Without this, a promoted ticket has no gmail_message_id and the
    poller's own already-processed check can never recognize it, risking a
    duplicate ticket on a later poll."""
    app = _app()
    client = TestClient(app)
    mock_run = AsyncMock()

    def fake_select(table, params=None):
        if table == "brands":
            return [{"id": BRAND_ID, "tenant_id": TENANT_ID, "is_active": True, "gmail_connected": True}]
        if table == "email_quarantine":
            return [_quarantine_row(gmail_message_id="msg-42")]
        return []

    with patch("src.api.routes.v2_quarantine.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_quarantine.supabase_update", return_value=[{"id": QID}]), \
         patch("src.api.routes.v2_quarantine._run_promotion", new=mock_run):
        resp = client.post(f"/api/v1/quarantine/{QID}/promote")

    assert resp.status_code == 200
    mock_run.assert_awaited_once()
    payload = mock_run.await_args.args[1]
    assert payload["gmail_message_id"] == "msg-42"


@pytest.mark.asyncio
async def test_run_promotion_releases_claim_on_failure():
    """Safety net for the now-backgrounded work: a failed AI/email pipeline
    must still release the quarantine record back to pending, not lose it
    stuck in 'promoted' forever."""
    with patch("src.workers.message_processor.message_processor.process_message",
               new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch("src.api.routes.v2_quarantine.supabase_update") as mock_update:
        await _run_promotion(QID, {"channel": "email", "customer_email": "c@example.com"}, "agent@example.com")

    mock_update.assert_called_once_with("email_quarantine", {"id": f"eq.{QID}"}, {"status": "pending"})


@pytest.mark.asyncio
async def test_run_promotion_creates_ticket_on_success():
    with patch("src.workers.message_processor.message_processor.process_message",
               new=AsyncMock(return_value={"ticket_id": "ticket-99"})), \
         patch("src.api.routes.v2_quarantine.supabase_update") as mock_update:
        await _run_promotion(QID, {"channel": "email", "customer_email": "c@example.com"}, "agent@example.com")

    mock_update.assert_not_called()  # success path never touches email_quarantine again


# ── 2. A discarded item is never re-surfaced by the poller ─────────────────

async def _run_poll(brand, emails, quarantine_rows):
    poller = EmailPoller()
    fake_filter_result = MagicMock(decision="blocked", reason="test-boundary", auto_reply_enabled=False)

    def fake_select(table, params=None):
        if table == "tickets":
            return []
        if table == "email_quarantine":
            return quarantine_rows
        return []

    with patch("src.services.brand_gmail_service.brand_gmail_service.get_new_emails",
               new=AsyncMock(return_value=emails)), \
         patch("src.channels.email_poller.email_filter_service.evaluate", return_value=fake_filter_result) as mock_evaluate, \
         patch("src.channels.email_poller.email_filter_service.log_decision"), \
         patch("src.channels.email_poller.supabase_select", side_effect=fake_select):
        await poller._poll_brand_inbox(brand)

    return [call.args[0]["sender_email"] for call in mock_evaluate.call_args_list]


def _brand():
    return {
        "id": BRAND_ID, "name": "Test Brand", "agent_name": "Luna",
        "gmail_email": "brand@example.com", "support_email": "support@example.com",
    }


@pytest.mark.asyncio
async def test_discarded_message_is_skipped_before_reevaluation():
    discarded_email = {
        "id": "msg-1", "thread_id": "t1", "subject": "Where is my order?",
        "sender_name": "Customer", "sender_email": "customer@example.com",
        "body": "Hey, where's my order?", "label_ids": [],
    }
    quarantine_rows = [{"id": QID, "gmail_message_id": "msg-1", "status": "discarded"}]

    reached_filter = await _run_poll(_brand(), [discarded_email], quarantine_rows)

    assert reached_filter == []  # never re-evaluated, so it can never come back as a ticket


@pytest.mark.asyncio
async def test_pending_message_is_still_evaluated_normally():
    """Regression guard - the discard check must not block ordinary new
    mail that has no quarantine record at all."""
    normal_email = {
        "id": "msg-2", "thread_id": "t2", "subject": "Where is my order?",
        "sender_name": "Customer", "sender_email": "customer@example.com",
        "body": "Hey, where's my order?", "label_ids": [],
    }

    reached_filter = await _run_poll(_brand(), [normal_email], quarantine_rows=[])

    assert "customer@example.com" in reached_filter
