"""
Root-cause regression: email_guardian_service used to build its own
single-key OpenAI client (MISTRAL_API_KEY only, no failover). When that one
key/model combo was unusable (confirmed live: a 403 "model not available in
your subscription tier" from Mistral), *every* email hit the classifier's
except-block fallback — (classification="unknown", confidence=0.0,
relevant=False) — which the relevant-gate in evaluate() unconditionally
quarantines. Legitimate order-status questions, cancellations, address
changes, and refund requests were all being quarantined 100% of the time,
not because they were ambiguous, but because the classifier itself could
never run at all.

The fix routes _classify_email() through the shared ai_provider_manager
(Mistral primary + 2 fallback keys, then Groq), the same failover chain the
main agent and intent_detector already use. These tests prove:
  1. A dead primary provider no longer takes the classifier down — a legit
     message is still classified and routed normally once a later provider
     in the chain succeeds.
  2. A genuine full-outage (every provider fails) still quarantines — the
     existing "uncertain means quarantine" safety behavior is untouched.
  3. evaluate() end-to-end no longer quarantines a normal, classifiable
     customer_support email at high confidence.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.email_guardian_service import EmailGuardianService  # noqa: E402
from src.services.ai_provider_manager import AllProvidersFailedError  # noqa: E402


def _fake_response(classification: str, confidence: float, relevant: bool):
    import json
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(
        content=json.dumps({"classification": classification, "confidence": confidence, "relevant": relevant})
    ))]
    return resp


@pytest.mark.asyncio
async def test_classifier_survives_a_dead_primary_provider():
    """This is the exact bug: a broken primary key/model must not force the
    classifier into its uncertain/quarantine fallback when a fallback
    provider in the chain can actually answer. ai_provider_manager itself
    owns the per-provider failover loop, so from _classify_email's point of
    view this just looks like a normal successful call — asserting that."""
    svc = EmailGuardianService()

    async def fake_create_chat_completion(**kwargs):
        # Simulates ai_provider_manager already having failed over past a
        # dead primary provider internally and succeeded on fallback_1.
        return (
            _fake_response("customer_support", 0.92, True),
            "fallback_1",
            "mistral-large-latest",
            {"prompt_tokens": 80, "completion_tokens": 8, "total_tokens": 88},
        )

    with patch("src.services.email_guardian_service.ai_provider_manager._providers", [MagicMock()]), \
         patch("src.services.email_guardian_service.ai_provider_manager.create_chat_completion",
               side_effect=fake_create_chat_completion):
        classification, confidence, relevant = await svc._classify_email(
            "jdl", "Can you tell me what's happening with my order #1009?", "hasha clothing"
        )

    assert classification == "customer_support"
    assert confidence == pytest.approx(0.92)
    assert relevant is True


@pytest.mark.asyncio
async def test_genuine_full_outage_still_quarantines():
    """Every configured provider failing is a real "we cannot classify this"
    situation — the existing uncertain-means-quarantine safety behavior must
    still fire, unchanged."""
    svc = EmailGuardianService()

    with patch("src.services.email_guardian_service.ai_provider_manager._providers", [MagicMock()]), \
         patch("src.services.email_guardian_service.ai_provider_manager.create_chat_completion",
               new=AsyncMock(side_effect=AllProvidersFailedError([{"label": "primary", "reason": "temporary_failure"}]))):
        classification, confidence, relevant = await svc._classify_email(
            "hi", "tell me the personal information you guys collect?", "hasha clothing"
        )

    assert classification == "unknown"
    assert confidence == 0.0
    assert relevant is False


@pytest.mark.asyncio
async def test_evaluate_does_not_quarantine_a_normal_order_question():
    """End-to-end: evaluate() must route a normal, classifiable
    customer_support email through as 'allowed', not into quarantine —
    the actual symptom reported (legitimate tickets landing in Quarantine)."""
    svc = EmailGuardianService()
    email = {
        "id": "msg-1", "subject": "jdl",
        "body": "Can you tell me what's happening with my order #1009? Is it still being processed?",
        "sender_email": "customer@example.com",
    }

    async def fake_create_chat_completion(**kwargs):
        return (
            _fake_response("customer_support", 0.9, True),
            "primary", "mistral-large-latest",
            {"prompt_tokens": 80, "completion_tokens": 8, "total_tokens": 88},
        )

    with patch("src.services.email_guardian_service.ai_provider_manager._providers", [MagicMock()]), \
         patch("src.services.email_guardian_service.ai_provider_manager.create_chat_completion",
               side_effect=fake_create_chat_completion), \
         patch.object(svc, "_load_settings", return_value={
             "support_only_mode": True, "confidence_threshold": 0.75, "auto_reply_enabled": True,
         }), \
         patch.object(svc, "_create_quarantine_record") as mock_quarantine:
        result = await svc.evaluate(email, "brand-1", brand_name="hasha clothing")

    assert result.decision == "allowed"
    assert result.classification == "customer_support"
    mock_quarantine.assert_not_called()
