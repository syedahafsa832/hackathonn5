"""
AI usage coverage extended to intent_detector.py (tResolv economics work,
continuation of the ai_provider_manager instrumentation).

IntentDetector.detect() uses its own raw OpenAI client (not
ai_provider_manager - a pre-existing, intentional separation, not something
this pass changes), so it needed its own usage capture rather than
inheriting the manager's. Mirrors the same shape/rules: prompt_tokens/
completion_tokens/total_tokens are None (never fabricated as 0) when the
provider's response has no usage block; latency_ms is always real.

Not persisted to ai_conversations (that table logs one row per
customer-facing message; intent detection is a separate, internal
classification call) - logged instead via the existing "[Intent] LLM -> ..."
line, which these tests verify via caplog rather than a DB write.
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
import json  # noqa: E402
from src.services.intent_detector import IntentDetector  # noqa: E402


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _fake_response(payload: dict, usage: dict | None):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    resp.usage = MagicMock(**usage) if usage is not None else None
    return resp


def _detector_with_client(client):
    d = IntentDetector.__new__(IntentDetector)
    d._client = client
    return d


def test_usage_captured_from_successful_llm_response():
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {"action_type": "cancel", "order_id": "1001", "confidence": 0.9},
        usage={"prompt_tokens": 200, "completion_tokens": 15, "total_tokens": 215},
    )
    detector = _detector_with_client(client)

    result = run(detector.detect("please cancel my order"))

    assert result.source == "llm"
    assert result.usage is not None
    assert result.usage["total_tokens"] == 215
    assert result.usage["prompt_tokens"] == 200
    assert isinstance(result.usage["latency_ms"], int)


def test_usage_is_none_not_zero_when_provider_omits_it():
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {"action_type": "cancel", "order_id": "1001", "confidence": 0.9}, usage=None,
    )
    detector = _detector_with_client(client)

    result = run(detector.detect("please cancel my order"))

    assert result.usage["total_tokens"] is None
    assert result.usage["prompt_tokens"] is None


def test_keyword_fallback_has_no_usage():
    """No LLM client at all -> pure keyword fallback -> no API call was made,
    so there's nothing to report usage for."""
    detector = IntentDetector.__new__(IntentDetector)
    detector._client = None

    with patch.object(IntentDetector, "_get_client", return_value=None):
        result = run(detector.detect("please cancel my order 1001"))

    assert result.source == "fallback"
    assert result.usage is None


def test_usage_logged_with_real_token_count(caplog):
    import logging
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {"action_type": "refund", "order_id": "2002", "confidence": 0.8},
        usage={"prompt_tokens": 300, "completion_tokens": 20, "total_tokens": 320},
    )
    detector = _detector_with_client(client)

    with caplog.at_level(logging.INFO):
        run(detector.detect("refund order 2002"))

    assert any("tokens=320" in r.message for r in caplog.records)
