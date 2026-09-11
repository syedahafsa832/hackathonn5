"""
Empty-reply_body-in-valid-JSON bug (P0): Cloudflare Workers AI
(@cf/meta/llama-3.3-70b-instruct-fp8-fast) was observed returning HTTP 200
with syntactically valid, schema-complete JSON but reply_body="" - on
unrelated messages ("hey tell me about your brand" and a product question
alike), with tokens_used unchanged by an earlier max_tokens fix, ruling out
truncation. ai_provider_manager.create_chat_completion() had no way to tell
this apart from a genuine success, so it was accepted, sent, and persisted
as if it were a real answer (later papered over by an unrelated "never send
an empty draft" safety net with a generic greeting - a fallback message,
not the customer's actual answer).

Fix: an optional validate_response callback runs on the raw response
BEFORE it's accepted - a validation failure is treated exactly like a
network/auth failure (logged, move to the next configured provider), never
a new/separate retry mechanism. customer_success_agent.py's
_validate_ai_json_reply is schema-aware (checks reply_body specifically);
ai_provider_manager.py itself stays schema-agnostic (other callers that
don't pass a validator are completely unaffected).
"""
import os
import sys
import json
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.ai_provider_manager import AIProviderManager, AllProvidersFailedError, _Provider  # noqa: E402
from src.agent.customer_success_agent import _validate_ai_json_reply  # noqa: E402


def _fake_response(text="ok"):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=text))]
    resp.usage = None
    return resp


def _manager_with(*labels):
    mgr = AIProviderManager.__new__(AIProviderManager)
    mgr._providers = [_Provider(label, f"key-{label}", "some-model") for label in labels]
    mgr._clients = {}
    return mgr


VALID_JSON = json.dumps({"intent": "product_inquiry", "reply_body": "Yes, it's $49.99!", "risk_level": "low"})
EMPTY_REPLY_JSON = json.dumps({"thought_process": "...", "reply_body": "", "actions": []})
WHITESPACE_REPLY_JSON = json.dumps({"reply_body": "   \n  "})
MALFORMED_JSON = '{"reply_body": "Yes, in stock!"'  # missing closing brace


# ── 1/2/3: _validate_ai_json_reply unit behavior ─────────────────────────

def test_valid_non_empty_reply_body_passes():
    assert _validate_ai_json_reply(_fake_response(VALID_JSON)) is None


def test_empty_reply_body_is_flagged():
    assert _validate_ai_json_reply(_fake_response(EMPTY_REPLY_JSON)) == "empty_reply_body"


def test_whitespace_only_reply_body_is_flagged():
    assert _validate_ai_json_reply(_fake_response(WHITESPACE_REPLY_JSON)) == "empty_reply_body"


def test_completely_empty_raw_content_is_flagged():
    assert _validate_ai_json_reply(_fake_response("")) == "empty_reply_body"
    assert _validate_ai_json_reply(_fake_response(None)) == "empty_reply_body"


# ── 4: malformed JSON is NOT this validator's concern (existing downstream
#      json.JSONDecodeError -> _get_fallback_response() path is untouched) ──

def test_malformed_json_is_not_flagged_by_the_validator():
    assert _validate_ai_json_reply(_fake_response(MALFORMED_JSON)) is None


# ── 5/6: provider-level integration - empty reply_body triggers fallback,
#         a working fallback provider's real answer reaches the caller ────

@pytest.mark.asyncio
async def test_empty_reply_body_triggers_fallback_to_next_provider():
    mgr = _manager_with("cloudflare_fallback_1", "primary")
    bad_client = MagicMock()
    bad_client.chat.completions.create.return_value = _fake_response(EMPTY_REPLY_JSON)
    mgr._clients["cloudflare_fallback_1"] = bad_client
    good_client = MagicMock()
    good_client.chat.completions.create.return_value = _fake_response(VALID_JSON)
    mgr._clients["primary"] = good_client

    with patch("src.services.ai_provider_manager.asyncio.sleep", new=AsyncMock(return_value=None)):
        response, label, model, usage = await mgr.create_chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            validate_response=_validate_ai_json_reply,
        )

    # The bad provider was tried and rejected; the customer gets the
    # WORKING fallback's real answer, not the empty one.
    assert label == "primary"
    assert json.loads(response.choices[0].message.content)["reply_body"] == "Yes, it's $49.99!"
    bad_client.chat.completions.create.assert_called_once()
    good_client.chat.completions.create.assert_called_once()


@pytest.mark.asyncio
async def test_validator_not_provided_preserves_old_behavior_for_other_callers():
    """intent_detector.py and other callers that never pass validate_response
    are completely unaffected - an empty reply_body is still accepted as a
    plain success exactly as before this fix existed."""
    mgr = _manager_with("primary")
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(EMPTY_REPLY_JSON)
    mgr._clients["primary"] = client

    response, label, model, usage = await mgr.create_chat_completion(messages=[{"role": "user", "content": "hi"}])
    assert label == "primary"
    assert json.loads(response.choices[0].message.content)["reply_body"] == ""


# ── 7: all providers fail validation -> existing safe AllProvidersFailedError
#      path, never an empty response silently accepted as success ──────────

@pytest.mark.asyncio
async def test_all_providers_returning_empty_reply_body_raises_not_silently_succeeds():
    mgr = _manager_with("cloudflare_fallback_1", "primary")
    for label in ("cloudflare_fallback_1", "primary"):
        client = MagicMock()
        client.chat.completions.create.return_value = _fake_response(EMPTY_REPLY_JSON)
        mgr._clients[label] = client

    with patch("src.services.ai_provider_manager.asyncio.sleep", new=AsyncMock(return_value=None)):
        with pytest.raises(AllProvidersFailedError) as exc_info:
            await mgr.create_chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                validate_response=_validate_ai_json_reply,
            )

    # Every attempt's reason is recorded (observability) and every reason is
    # the validation failure, not a fabricated network error.
    reasons = {a["reason"] for a in exc_info.value.attempts}
    assert reasons == {"empty_reply_body"}
