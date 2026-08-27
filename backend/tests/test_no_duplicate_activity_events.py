"""
Activity timeline duplicates (reported bug): a ticket's Activity feed showed
"New customer message received" twice and "Draft ready" / "Draft ready for
your team to review" three times total for a single processing run.

Root cause: message_processor.py already logs its own coarse milestones via
_log_ticket_event() at the right points -
    "message_received" -> "New customer message received"   (STAGE 1.8, before the agent runs)
    "draft_ready"       -> "Draft ready"                     (STAGE 5, right after the agent returns)
    "needs_review"      -> "Draft ready for your team to review"  (STAGE 10, when the draft won't auto-send)
customer_success_agent.process_customer_query() ALSO called on_progress with
its own "received"/"New customer message received" (as its very first emit)
and "draft_ready"/"Draft ready for your team to review" (right before
returning) - both fully redundant, since message_processor.py's on_progress
wiring (see test_realtime_ticket_events.py) persists every one of the
agent's own emits as an additional ticket_events row. One real processing
run therefore wrote two "received" rows and three "draft ready"-ish rows.

Fix: removed both emits from process_customer_query - message_processor.py's
own milestones already cover both moments, more accurately (its
"message_received" fires at true ticket intake, before RAG/tool setup even
starts). This is a backend fix (fewer ticket_events rows actually get
written), not a frontend de-duplication.
"""
import os
import sys
import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402

# The exact coarse-milestone labels message_processor.py owns (STAGE 1.8/5/10
# — see _log_ticket_event call sites there). The agent's own on_progress
# emissions must never reproduce any of these verbatim.
_MESSAGE_PROCESSOR_OWNED_LABELS = {
    "New customer message received",
    "Draft ready",
    "Draft ready for your team to review",
}


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _fake_ai_response(reply_body: str):
    msg = MagicMock()
    msg.content = json.dumps({"intent": "general_inquiry", "reply_body": reply_body, "risk_level": "low"})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


NO_ACTION = IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm")
_FAKE_USAGE = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 1, "attempts": 1}


def _run_agent(query="hi there!", store_id="brand-1"):
    events = []

    async def on_progress(stage, label):
        events.append((stage, label))

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion",
               new=AsyncMock(return_value=(_fake_ai_response("Here you go!"), "test_provider", "test_model", _FAKE_USAGE))), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=NO_ACTION)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.lib.supabase_client.supabase_select", return_value=[]):
        run(customer_success_agent.process_customer_query(
            query=query,
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "email"},
            tenant_id="tenant-1",
            store_id=store_id,
            ticket_id="ticket-1",
            on_progress=on_progress,
        ))
    return events


def test_agent_never_emits_message_processors_own_received_milestone():
    """message_processor.py already logs 'New customer message received'
    before calling the agent at all - the agent's own on_progress chain
    must never emit that exact label a second time."""
    events = _run_agent()
    labels = {label for _, label in events}
    assert "New customer message received" not in labels


def test_agent_never_emits_message_processors_own_draft_ready_milestones():
    """message_processor.py already logs 'Draft ready' right after the
    agent returns, and 'Draft ready for your team to review' once it knows
    the ticket needs human review - the agent's own on_progress chain must
    never emit either label itself."""
    events = _run_agent()
    labels = {label for _, label in events}
    assert "Draft ready" not in labels
    assert "Draft ready for your team to review" not in labels


def test_agent_emitted_labels_never_collide_with_message_processor_labels():
    """Blanket guard: whatever the agent's own emit chain produces (order
    lookup, kb_check, style_check, policy_check, preparing, ...), none of it
    may reproduce a label message_processor.py's own coarse milestones own -
    each real event should be written exactly once, by exactly one layer."""
    events = _run_agent()
    labels = {label for _, label in events}
    overlap = labels & _MESSAGE_PROCESSOR_OWNED_LABELS
    assert not overlap, f"Agent emitted label(s) already owned by message_processor.py: {overlap}"


def test_a_single_run_produces_each_agent_stage_at_most_once():
    """One processing run of one message must never emit the same stage key
    twice - a duplicate stage from the agent's own chain would double up in
    the persisted ticket_events / Activity timeline exactly like the
    reported bug, independent of which label text is used."""
    events = _run_agent()
    stages = [stage for stage, _ in events]
    assert len(stages) == len(set(stages)), f"Duplicate stage(s) emitted in a single run: {stages}"
