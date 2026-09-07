"""
Production bug: "Hi Luna, am i allowed to do exchanges? i want a better
color this one sucks" — an unambiguous customer-support message — was
placed in Quarantine, shown in the UI as "Unknown · 0%".

ROOT CAUSE: CLASSIFIER_PROMPT (email_guardian_service.py) told the model
only the Shopify store's own brand_name — never the brand's configured AI
support agent name (brands.agent_name, e.g. "Luna", already used
everywhere else in the reply-generation pipeline — see
customer_success_agent.py). A customer addressing the agent directly
("Hi Luna, ...") gives the classifier no way to recognize that as a
message TO this brand's own inbox; without that context a model may
reasonably conclude the message is addressed to some unrelated third
party named "Luna" and mark relevant=false — which routes straight into
the "not relevant to this brand" noise-gate in evaluate(), producing
exactly the observed "unknown · 0%, quarantined" outcome for an obviously
real support request.

FIX: thread the brand's agent_name through
EmailGuardianService.evaluate()/_classify_email() into CLASSIFIER_PROMPT,
with explicit instructions that a message addressed to the agent by name
is addressed to the brand, never irrelevant/automation just because of
that. email_poller.py's call site now passes brand.get("agent_name").

This does NOT touch the confidence gate, the BLOCKED_CLASSIFICATIONS set,
the promotional/system-notification keyword filters, or the deliberate
"classifier totally unavailable -> quarantine, never auto-allow" safety
behavior (test_email_guardian_failover.py's
test_genuine_full_outage_still_quarantines) - only the classifier's own
input context changed.
"""
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.email_guardian_service import EmailGuardianService  # noqa: E402

_DEFAULT_SETTINGS = {"support_only_mode": True, "confidence_threshold": 0.75, "auto_reply_enabled": True}


def _email(subject, body, msg_id="msg-1"):
    return {"id": msg_id, "subject": subject, "body": body, "sender_email": "customer@example.com"}


async def _evaluate(classify_result, subject, body, brand_name="Acme", agent_name="Luna"):
    svc = EmailGuardianService()
    with patch.object(svc, "_load_settings", return_value=_DEFAULT_SETTINGS), \
         patch.object(svc, "_find_existing_decision", return_value=None), \
         patch.object(svc, "_classify_email", new=AsyncMock(return_value=classify_result)), \
         patch.object(svc, "_create_quarantine_record", return_value="q-1") as mock_create:
        result = await svc.evaluate(_email(subject, body), "brand-1", brand_name=brand_name, agent_name=agent_name)
    return result, mock_create


# ── 1-5. Genuine customer-support messages → ticket (allowed) ──────────────
# Each mocks the classifier's OUTPUT to what a correctly-informed model
# (given the new agent_name context) should now produce — evaluate()'s own
# routing logic is real and unmodified.

@pytest.mark.asyncio
async def test_hi_luna_exchange_color_complaint_is_allowed_not_quarantined():
    result, _ = await _evaluate(
        classify_result=("customer_support", 0.85, True),
        subject="Question",
        body="Hi Luna, am i allowed to do exchanges? i want a better color this one sucks",
    )
    assert result.decision == "allowed", (
        f"GAP: the reported production bug is not fixed — got {result.decision!r} instead of 'allowed'"
    )


@pytest.mark.asyncio
async def test_can_i_exchange_my_order_is_allowed():
    result, _ = await _evaluate(
        classify_result=("customer_support", 0.9, True),
        subject="Exchange",
        body="Can I exchange my order for another color?",
    )
    assert result.decision == "allowed"


@pytest.mark.asyncio
async def test_where_is_my_order_is_allowed():
    result, _ = await _evaluate(
        classify_result=("customer_support", 0.9, True),
        subject="Order status",
        body="Where is my order?",
    )
    assert result.decision == "allowed"


@pytest.mark.asyncio
async def test_refund_request_is_allowed():
    result, _ = await _evaluate(
        classify_result=("customer_support", 0.85, True),
        subject="Refund",
        body="I want a refund because this product isn't what I expected",
    )
    assert result.decision == "allowed"


@pytest.mark.asyncio
async def test_hi_luna_address_change_is_allowed():
    result, _ = await _evaluate(
        classify_result=("customer_support", 0.85, True),
        subject="Address",
        body="Hi Luna, can you help me change my shipping address?",
    )
    assert result.decision == "allowed"


# ── 6-7. Real noise still blocked (spam/marketing filtering intact) ────────

@pytest.mark.asyncio
async def test_real_marketing_newsletter_is_blocked():
    result, mock_create = await _evaluate(
        classify_result=("newsletter", 0.95, False),
        subject="This week's top picks + 20% off everything",
        body="Unsubscribe | View in browser | Manage preferences",
    )
    assert result.decision == "blocked"
    args, kwargs = mock_create.call_args
    assert (args[4] if len(args) > 4 else kwargs.get("status")) == "auto_blocked"


@pytest.mark.asyncio
async def test_real_automated_notification_is_blocked():
    result, mock_create = await _evaluate(
        classify_result=("automation", 0.95, False),
        subject="Your shipment has been delivered",
        body="This is an automated notification. Your package was delivered today.",
    )
    assert result.decision == "blocked"
    args, kwargs = mock_create.call_args
    assert (args[4] if len(args) > 4 else kwargs.get("status")) == "auto_blocked"


# ── 8. Genuinely ambiguous → quarantine (existing routing, unchanged) ──────

@pytest.mark.asyncio
async def test_genuinely_ambiguous_message_is_quarantined():
    result, mock_create = await _evaluate(
        classify_result=("unknown", 0.3, False),
        subject="???",
        body="k",
    )
    assert result.decision == "quarantined"
    args, kwargs = mock_create.call_args
    assert len(args) <= 4 and "status" not in kwargs, (
        "genuinely ambiguous content must land in 'pending' (quarantine), not auto_blocked"
    )


# ── 9. Existing confidence-gated behavior still intact ─────────────────────

@pytest.mark.asyncio
async def test_confident_noise_is_blocked():
    result, mock_create = await _evaluate(classify_result=("spam", 0.95, False), subject="x", body="x")
    assert result.decision == "blocked"
    args, kwargs = mock_create.call_args
    assert (args[4] if len(args) > 4 else kwargs.get("status")) == "auto_blocked"


@pytest.mark.asyncio
async def test_uncertain_noise_is_quarantined():
    result, mock_create = await _evaluate(classify_result=("spam", 0.5, False), subject="x", body="x")
    assert result.decision == "quarantined"
    args, kwargs = mock_create.call_args
    assert len(args) <= 4 and "status" not in kwargs


@pytest.mark.asyncio
async def test_confident_customer_support_is_a_ticket():
    result, _ = await _evaluate(classify_result=("customer_support", 0.9, True), subject="x", body="Where is my order?")
    assert result.decision == "allowed"


# ── Root-cause proof: the classifier prompt now carries agent_name ─────────

def _fake_response(classification: str, confidence: float, relevant: bool):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(
        content=json.dumps({"classification": classification, "confidence": confidence, "relevant": relevant})
    ))]
    return resp


@pytest.mark.asyncio
async def test_classifier_prompt_tells_the_model_the_agents_name():
    """Proves the actual fix, not just the routing: the prompt text sent to
    the LLM must name the brand's configured agent so a message addressed
    to "Luna" is recognized as addressed to the brand, not to an unrelated
    third party (the concrete root cause of the reported bug)."""
    svc = EmailGuardianService()
    captured = {}

    async def fake_create_chat_completion(**kwargs):
        captured["messages"] = kwargs["messages"]
        return (
            _fake_response("customer_support", 0.85, True),
            "primary", "mistral-large-latest",
            {"prompt_tokens": 80, "completion_tokens": 8, "total_tokens": 88},
        )

    with patch("src.services.email_guardian_service.ai_provider_manager._providers", [MagicMock()]), \
         patch("src.services.email_guardian_service.ai_provider_manager.create_chat_completion",
               side_effect=fake_create_chat_completion):
        classification, confidence, relevant = await svc._classify_email(
            "Question", "Hi Luna, am i allowed to do exchanges?", "Acme", "Luna"
        )

    assert classification == "customer_support"
    assert relevant is True
    prompt_text = captured["messages"][0]["content"]
    assert "Luna" in prompt_text, "GAP: the classifier prompt never mentions the agent's name at all"
    assert "own AI support agent" in prompt_text or "addressed to \"Luna\"" in prompt_text, (
        "GAP: the prompt mentions 'Luna' but doesn't explain it's this brand's own agent - "
        "the model still has no way to know a message to 'Luna' is a message to the brand"
    )


@pytest.mark.asyncio
async def test_classifier_defaults_agent_name_to_luna_when_brand_has_none_configured():
    """A brand with no custom agent_name set (agent_name column NULL) must
    still get sensible prompt context, not an empty/broken prompt."""
    svc = EmailGuardianService()
    captured = {}

    async def fake_create_chat_completion(**kwargs):
        captured["messages"] = kwargs["messages"]
        return (
            _fake_response("customer_support", 0.85, True),
            "primary", "mistral-large-latest",
            {"prompt_tokens": 80, "completion_tokens": 8, "total_tokens": 88},
        )

    with patch("src.services.email_guardian_service.ai_provider_manager._providers", [MagicMock()]), \
         patch("src.services.email_guardian_service.ai_provider_manager.create_chat_completion",
               side_effect=fake_create_chat_completion):
        await svc._classify_email("Question", "Hi Luna, can you help?", "Acme", None)

    assert "Luna" in captured["messages"][0]["content"]
