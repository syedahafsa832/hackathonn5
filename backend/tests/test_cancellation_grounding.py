"""
Cancellation-flow grounding fixes.

1. Customer identity: for the email channel, customer_info["name"] is sourced
   from the real inbound email's sender display name (email_poller.py:
   customer_name = email["sender_name"]) - confirmed by inspection, not
   invented by the AI. _construct_v3_prompt() must pass it through verbatim,
   never embellish or substitute it.

2. Policy grounding: the prompt previously had no rule against inventing
   specific policy details (a time window, a cutoff, a fee) that aren't
   backed by KNOWLEDGE BASE or RETURN/EXCHANGE STATUS - which is how "if you
   placed your order within the last two hours..." got said despite no such
   rule existing anywhere in the codebase (grepped: zero references to any
   order-age-based cancellation window, in code or RAG-import content).
   ACTION RULES item 6 closes that gap.

3. When order_id/email aren't both present yet (e.g. "what do you need to
   cancel my order in real time?"), the existing return_actions_integration.py
   path already asks for both rather than guessing or inventing timing -
   this test locks that in as a regression guard for the exact real-world
   phrasing that previously got a "within two hours" answer instead.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent  # noqa: E402
from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402


def run(coro):
    # See test_chat_widget_action_staging.py's run() for why: asyncio.get_
    # event_loop() can return an already-closed loop left behind by an
    # earlier @pytest.mark.asyncio test in the full suite.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def test_prompt_forbids_inventing_specific_policy_details():
    prompt = customer_success_agent._construct_v3_prompt(
        customer_info={"name": "Jane Doe", "email": "jane@example.com"},
        rag_context="", sizing_context="", tool_context="", action_context="",
    )
    lowered = prompt.lower()
    assert "never invent a specific policy detail" in lowered
    assert "time window" in lowered


def test_customer_name_in_prompt_is_exactly_what_was_provided_never_embellished():
    prompt = customer_success_agent._construct_v3_prompt(
        customer_info={"name": "Syeda Hafsa", "email": "syeda@example.com"},
        rag_context="", sizing_context="", tool_context="", action_context="",
    )
    assert "Name: Syeda Hafsa" in prompt
    # No fabricated honorific/title appended beyond the literal name given.
    assert "Dr. Syeda Hafsa" not in prompt
    assert "Ms. Syeda Hafsa" not in prompt


def test_cancellation_request_without_order_id_or_email_asks_for_both_not_a_guessed_timeline():
    """Real-world query: 'hi, what do you need to cancel my order in real
    time?' - no order number, no confirmed email in the message itself.
    Must ask for verification, never invent an eligibility window."""
    integration = ReturnActionsIntegration()
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock()) as mock_eligibility:
        result = run(integration.handle_return_intent(
            query="hi, what do you need to cancel my order in real time?",
            customer_info={"name": "Syeda Hafsa", "email": None},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=IntentResult(action_type="cancel", order_id=None, raw_address=None, confidence=0.8, source="llm"),
        ))

    # Never reaches Shopify without both pieces of verification - nothing to
    # check eligibility against yet.
    mock_eligibility.assert_not_called()
    context = result["action_context"].lower()
    assert "order number" in context and "email" in context
    assert "do not assume or guess" in context
    # The specific unfounded claim this bug produced must not appear anywhere
    # in what the code hands the model.
    assert "two hour" not in context
    assert "2 hour" not in context
