"""
Regression test for the Escalations-triage task's wording fix.

Previously, when a return/exchange request was already escalated (the
specialist's action_context contains the literal marker "ESCALATE TO HUMAN,
NO ACTION CREATED" - see return_specialist.py / exchange_specialist.py), the
shared prompt's ACTION RULES only ever recognized "ACTION STAGED FOR
APPROVAL" (rule 2) as meaning something had happened - everything else fell
through to "nothing has been submitted, ask for what's missing", producing
exactly the observed bug: the AI asking "would you like me to escalate
this?" even though escalation had already happened.

This does not call the LLM - it asserts on the literal prompt text
_construct_v3_prompt() builds, so it's deterministic and fast.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.agent.customer_success_agent import CustomerSuccessAgent  # noqa: E402

EXCHANGE_ESCALATED_NOTE = (
    "**EXCHANGE REQUEST - ESCALATE TO HUMAN, NO ACTION CREATED**: Exchanges are not automated "
    "yet, and this order is outside the exchange window (order not found within eligible range). "
    "Do NOT say a refund, cancellation, or exchange was started, staged, or submitted for this - "
    "none was. Tell the customer honestly that you've noted their exchange request and a team "
    "member will follow up to confirm eligibility and next steps."
)


def _prompt(action_context):
    agent = CustomerSuccessAgent()
    return agent._construct_v3_prompt(
        customer_info={"name": "Bushra Zohaib", "email": "bushrazohaib84@gmail.com"},
        rag_context="", sizing_context="", tool_context="", action_context=action_context,
    )


def test_already_escalated_exchange_tells_model_not_to_ask_permission():
    prompt = _prompt(EXCHANGE_ESCALATED_NOTE)
    assert "ESCALATE TO HUMAN, NO ACTION CREATED" in prompt
    # The new rule must explicitly forbid the exact hedging phrasing observed
    # in the ticket ("would you like me to escalate this?").
    assert 'would you like me to escalate' in prompt.lower()
    assert 'do not ask' in prompt.lower() or 'do not ask' in prompt.lower().replace("n't", "not")
    assert "already been sent to our support team" in prompt.lower()


def test_empty_action_context_still_asks_for_whats_missing_unchanged():
    """Rule 2's original behavior (nothing staged, nothing escalated yet)
    must be untouched by the new rule 3."""
    prompt = _prompt("")
    assert 'NOTHING has been submitted yet' in prompt


def test_staged_for_approval_marker_still_recognized_unchanged():
    prompt = _prompt("Refund request: **ACTION STAGED FOR APPROVAL**. Awaiting merchant review.")
    assert "ACTION STAGED FOR APPROVAL" in prompt
    assert "I've prepared your request and sent it to our team for confirmation" in prompt
