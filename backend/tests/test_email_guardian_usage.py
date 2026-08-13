"""
AI usage coverage extended to email_guardian_service.py's classifier call
(tResolv economics work, continuation of the ai_provider_manager
instrumentation).

_classify_email() keeps its existing (classification, confidence, relevant)
return contract unchanged - real callers already unpack exactly 3 values,
and this is an internal spam/quarantine gate that never reaches
ai_conversations, so widening the return type wasn't worth the churn. Usage
is logged instead via the existing "[Guardian] Classifier -> ..." line,
following the same "never fabricate 0" rule as the other two instrumented
call sites.
"""
import os
import sys
import json
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import logging  # noqa: E402
from src.services.email_guardian_service import EmailGuardianService  # noqa: E402


def _fake_response(payload: dict, usage: dict | None):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    resp.usage = MagicMock(**usage) if usage is not None else None
    return resp


def _service_with_client(client):
    svc = EmailGuardianService.__new__(EmailGuardianService)
    svc._client = client
    return svc


def test_usage_logged_with_real_token_count(caplog):
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {"classification": "customer_support", "confidence": 0.95, "relevant": True},
        usage={"prompt_tokens": 150, "completion_tokens": 10, "total_tokens": 160},
    )
    svc = _service_with_client(client)

    with caplog.at_level(logging.INFO):
        classification, confidence, relevant = svc._classify_email("Order question", "Where is my order?", "Acme")

    assert classification == "customer_support"
    assert any("tokens=160" in r.message for r in caplog.records)


def test_usage_logged_as_none_not_zero_when_provider_omits_it(caplog):
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {"classification": "spam", "confidence": 0.9, "relevant": False}, usage=None,
    )
    svc = _service_with_client(client)

    with caplog.at_level(logging.INFO):
        svc._classify_email("Buy now", "cheap watches", "Acme")

    assert any("tokens=None" in r.message for r in caplog.records)


def test_return_contract_is_unchanged_three_values():
    """Regression guard: widening usage capture must never change the
    function's (classification, confidence, relevant) return shape - real
    callers unpack exactly 3 values."""
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {"classification": "customer_support", "confidence": 0.8, "relevant": True},
        usage={"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
    )
    svc = _service_with_client(client)

    result = svc._classify_email("Help", "I need help", "Acme")

    assert len(result) == 3
    classification, confidence, relevant = result
    assert classification == "customer_support"
