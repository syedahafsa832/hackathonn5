"""
Uploaded Examples were inert: generate_learned_profile() gated ALL learning
(approved replies + uploaded examples combined) behind
MIN_APPROVED_REPLIES_TO_LEARN=20, and regenerate_if_due()'s auto-trigger only
ever counted real approved replies. A merchant with a handful of hand-written
examples and <20 approved replies got "Need at least 20 approved replies"
forever, even with reply_style_use_uploaded_only=True - a toggle whose whole
purpose is to rely on curated examples instead of that volume.

Fix: any uploaded example present skips the volume gate entirely (both the
manual generate/regenerate path and the opportunistic auto-trigger). The
approved-replies-only threshold is unchanged when no examples are uploaded.
"""
import os
import sys
import json
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services import reply_style_service as svc  # noqa: E402

BRAND_ID = "brand-1"


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _brand(**overrides):
    b = {"id": BRAND_ID, "reply_style_use_uploaded_only": False, "reply_style_profile": None}
    b.update(overrides)
    return b


def _fake_select(brand=None, tickets=None, examples=None):
    def fn(table, params=None):
        if table == "brands":
            return [brand] if brand else []
        if table == "tickets":
            return tickets or []
        if table == "reply_style_examples":
            return examples or []
        return []
    return fn


PROFILE_JSON = json.dumps({
    "tone": "warm", "greeting_style": "first name", "closing_style": "brief",
    "emoji_usage": "rarely", "sentence_length": "short", "paragraph_style": "one paragraph",
    "use_bullets": False, "use_customer_name": "when natural", "reasoning": ["Detected casual tone"],
})


def _fake_llm_call(*args, **kwargs):
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=PROFILE_JSON))]
    return (response, "label", "model", {})


def test_uploaded_example_is_available_to_learning_with_zero_approved_replies():
    """The exact bug: a fresh example, no approved replies - previously
    blocked with 'Need at least 20 approved replies'."""
    examples = [{"id": "ex-1", "content": "Customer: Hey, can I change the size of my order?\n\nLuna's reply: Of course! If your order hasn't shipped yet, we can help."}]
    seen_prompts = {}

    def capturing_llm_call(*args, messages=None, **kwargs):
        seen_prompts["messages"] = messages
        return _fake_llm_call()

    with patch("src.services.reply_style_service.supabase_select", side_effect=_fake_select(brand=_brand(), examples=examples)), \
         patch("src.services.reply_style_service.supabase_update"), \
         patch("src.services.ai_provider_manager.ai_provider_manager",
               MagicMock(has_providers=True, create_chat_completion=AsyncMock(side_effect=capturing_llm_call))):
        result = run(svc.generate_learned_profile(BRAND_ID))

    assert result["success"] is True
    user_message = seen_prompts["messages"][1]["content"]
    assert "change the size of my order" in user_message  # example text actually reached the model


def test_zero_approved_and_zero_examples_still_blocked():
    """Regression guard: the 20-minimum still applies to organic learning
    when no examples have been uploaded - fix must not remove it outright."""
    with patch("src.services.reply_style_service.supabase_select", side_effect=_fake_select(brand=_brand(), examples=[])), \
         patch("src.services.reply_style_service.supabase_update"):
        result = run(svc.generate_learned_profile(BRAND_ID))

    assert result["success"] is False
    assert "20" in result["error"]


def test_use_uploaded_only_still_excludes_approved_replies():
    """reply_style_use_uploaded_only semantics preserved: with it set, real
    approved-reply text must never reach the extraction prompt."""
    tickets = [{"human_approved": True, "ai_reply": "REAL APPROVED REPLY TEXT", "updated_at": "2026-01-01"}] * 25
    examples = [{"id": "ex-1", "content": "Customer: Hi\n\nLuna's reply: UPLOADED EXAMPLE TEXT"}]
    seen_prompts = {}

    def capturing_llm_call(*args, messages=None, **kwargs):
        seen_prompts["messages"] = messages
        return _fake_llm_call()

    with patch("src.services.reply_style_service.supabase_select",
               side_effect=_fake_select(brand=_brand(reply_style_use_uploaded_only=True), tickets=tickets, examples=examples)), \
         patch("src.services.reply_style_service.supabase_update"), \
         patch("src.services.ai_provider_manager.ai_provider_manager",
               MagicMock(has_providers=True, create_chat_completion=AsyncMock(side_effect=capturing_llm_call))):
        result = run(svc.generate_learned_profile(BRAND_ID))

    assert result["success"] is True
    user_message = seen_prompts["messages"][1]["content"]
    assert "UPLOADED EXAMPLE TEXT" in user_message
    assert "REAL APPROVED REPLY TEXT" not in user_message


def test_style_block_never_contains_literal_example_text():
    """Examples influence style only (abstracted fields), never verbatim
    copy - the live prompt block can only ever contain the extracted
    descriptors, structurally proving Luna can't parrot an example back on
    an unrelated request."""
    profile = json.loads(PROFILE_JSON)
    profile.pop("reasoning")
    block = svc.build_style_prompt_block(profile)

    assert "change the size" not in block
    assert "Of course!" not in block
    assert "warm" in block  # the extracted tone descriptor, not raw example text


def test_regenerate_if_due_triggers_with_uploaded_example_and_no_approved_replies():
    """The opportunistic auto-trigger (checked on Settings page load) must
    also fire immediately off an uploaded example, not just approved-reply
    volume - otherwise the fix above never actually runs for a real user."""
    brand = _brand(reply_style_learn_automatically=True, reply_style_mode="preset")
    examples = [{"id": "ex-1", "content": "Customer: Hi\n\nLuna's reply: Hello! How can we help?"}]

    with patch("src.services.reply_style_service.supabase_select", side_effect=_fake_select(brand=brand, tickets=[], examples=examples)), \
         patch("src.services.reply_style_service.generate_learned_profile", new=AsyncMock()) as mock_generate:
        run(svc.regenerate_if_due(BRAND_ID))

    mock_generate.assert_awaited_once_with(BRAND_ID)


def test_regenerate_if_due_does_not_trigger_with_no_examples_and_few_approved():
    """Regression guard for the auto-trigger's own unchanged threshold path."""
    brand = _brand(reply_style_learn_automatically=True, reply_style_mode="preset")

    with patch("src.services.reply_style_service.supabase_select", side_effect=_fake_select(brand=brand, tickets=[], examples=[])), \
         patch("src.services.reply_style_service.generate_learned_profile", new=AsyncMock()) as mock_generate:
        run(svc.regenerate_if_due(BRAND_ID))

    mock_generate.assert_not_awaited()
