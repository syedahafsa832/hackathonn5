"""
AI usage coverage for email_guardian_service.py's classifier call (tResolv
economics work, continuation of the ai_provider_manager instrumentation).

_classify_email() keeps its existing (classification, confidence, relevant)
return contract unchanged - real callers already unpack exactly 3 values,
and this is an internal spam/quarantine gate that never reaches
ai_conversations, so widening the return type wasn't worth the churn. Usage
is logged instead via the existing "[Guardian] Classifier -> ..." line,
following the same "never fabricate 0" rule as the other two instrumented
call sites.

Classification now goes through the shared ai_provider_manager (Mistral +
Groq failover) instead of a single-key client of its own — see
test_email_guardian_failover.py for the regression coverage on *that* fix.
These tests mock ai_provider_manager.create_chat_completion directly.
"""
import os
import sys
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import logging  # noqa: E402
from src.services.email_guardian_service import EmailGuardianService  # noqa: E402


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    return resp


def _mock_create_chat_completion(payload: dict, usage: dict):
    """Matches ai_provider_manager.create_chat_completion's real return
    shape: (response, provider_label, model, usage)."""
    return AsyncMock(return_value=(_fake_response(payload), "primary", "mistral-large-latest", usage))


@pytest.mark.asyncio
async def test_usage_logged_with_real_token_count(caplog):
    svc = EmailGuardianService()
    mock_call = _mock_create_chat_completion(
        {"classification": "customer_support", "confidence": 0.95, "relevant": True},
        usage={"prompt_tokens": 150, "completion_tokens": 10, "total_tokens": 160},
    )

    with patch("src.services.email_guardian_service.ai_provider_manager._providers", [MagicMock()]), \
         patch("src.services.email_guardian_service.ai_provider_manager.create_chat_completion", mock_call), \
         caplog.at_level(logging.INFO):
        classification, confidence, relevant = await svc._classify_email("Order question", "Where is my order?", "Acme")

    assert classification == "customer_support"
    assert any("tokens=160" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_usage_logged_as_none_not_zero_when_provider_omits_it(caplog):
    svc = EmailGuardianService()
    mock_call = _mock_create_chat_completion(
        {"classification": "spam", "confidence": 0.9, "relevant": False},
        usage={"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
    )

    with patch("src.services.email_guardian_service.ai_provider_manager._providers", [MagicMock()]), \
         patch("src.services.email_guardian_service.ai_provider_manager.create_chat_completion", mock_call), \
         caplog.at_level(logging.INFO):
        await svc._classify_email("Buy now", "cheap watches", "Acme")

    assert any("tokens=None" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_return_contract_is_unchanged_three_values():
    """Regression guard: routing through ai_provider_manager must never
    change the function's (classification, confidence, relevant) return
    shape - real callers unpack exactly 3 values."""
    svc = EmailGuardianService()
    mock_call = _mock_create_chat_completion(
        {"classification": "customer_support", "confidence": 0.8, "relevant": True},
        usage={"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
    )

    with patch("src.services.email_guardian_service.ai_provider_manager._providers", [MagicMock()]), \
         patch("src.services.email_guardian_service.ai_provider_manager.create_chat_completion", mock_call):
        result = await svc._classify_email("Help", "I need help", "Acme")

    assert len(result) == 3
    classification, confidence, relevant = result
    assert classification == "customer_support"
