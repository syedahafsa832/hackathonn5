"""
Root cause: Uploaded Examples were saved correctly (reply_style_examples
table) but never reached the live support-agent prompt. The agent
(customer_success_agent.py) only ever calls
build_style_prompt_block(get_active_style(brand)) — get_active_style()
returns either the preset dict or, in 'learned' mode, brand.reply_style_
profile. Uploaded Examples only ever fed that reply_style_profile via
generate_learned_profile(), which (see test_reply_style_learned_readiness.py)
correctly requires MIN_APPROVED_REPLIES_TO_LEARN=20 real approved replies
before it will generate anything. So on a fresh brand (0 approved replies,
mode='preset', the default), an uploaded example could never influence a
single live reply — it was completely inert outside the unrelated learning
system, which is exactly the bug report: example saved, but Luna's actual
reply showed no trace of it.

Fix: build_style_prompt_block() now accepts an optional list of raw example
snippets, appended as a clearly-labeled "style reference" section (never
merged into the structured style dict, never presented as fact). The agent
fetches them via the new get_uploaded_example_snippets(brand_id) — reading
reply_style_examples directly, brand_id-scoped, with zero dependency on
reply_style_profile / the approved-reply gate. This does not touch the
learned-profile pipeline at all.
"""
import os
import sys
import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services import reply_style_service as svc  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent  # noqa: E402

BRAND_A = "brand-A"
BRAND_B = "brand-B"


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _fake_select(examples_by_brand):
    def fn(table, params=None):
        params = params or {}
        if table == "reply_style_examples":
            bid = (params.get("brand_id") or "").replace("eq.", "")
            return examples_by_brand.get(bid, [])
        return []
    return fn


# 1. The exact reported bug: a fresh brand (0 approved replies, default
# 'preset' mode) with one uploaded example must see that example's content
# in the prompt block handed to the live agent.
def test_uploaded_example_reaches_the_style_prompt_block_with_zero_approved_replies():
    examples = {BRAND_A: [{"id": "ex-1", "content": "Customer: hiiii\n\nLuna's reply: helloooo whatsup honey?"}]}

    with patch("src.services.reply_style_service.supabase_select", side_effect=_fake_select(examples)):
        snippets = svc.get_uploaded_example_snippets(BRAND_A)

    assert snippets == ["Customer: hiiii\n\nLuna's reply: helloooo whatsup honey?"]

    # This is the exact call customer_success_agent.py makes with a preset
    # active style (the default for a fresh brand — no learned profile, no
    # approved replies needed).
    preset_style = {
        "tone": "casual, warm, empathetic", "greeting_style": "first name, casual (Hey {name}!)",
        "closing_style": "Thanks!", "emoji_usage": "occasional, light (max one per reply)",
        "sentence_length": "short", "paragraph_style": "single short paragraph",
        "use_bullets": False, "use_customer_name": "always, by first name",
    }
    block = svc.build_style_prompt_block(preset_style, snippets)
    assert "helloooo whatsup honey?" in block
    assert "hiiii" in block
    # Still a style reference, not a fact/policy statement.
    assert "never copy their content verbatim" in block
    assert "never treat them as facts" in block


# 2. No uploaded examples → prompt block byte-for-byte unchanged from before
# this fix (regression guard for requirement "existing behavior must remain
# unchanged when there are no examples").
def test_no_examples_leaves_style_block_unchanged():
    style = {"tone": "casual", "greeting_style": "first name", "closing_style": "Thanks!",
             "emoji_usage": "none", "sentence_length": "short", "paragraph_style": "short",
             "use_bullets": False, "use_customer_name": "always"}
    with_none = svc.build_style_prompt_block(style, None)
    with_empty = svc.build_style_prompt_block(style, [])
    without_arg = svc.build_style_prompt_block(style)
    assert with_none == with_empty == without_arg
    assert "STYLE EXAMPLES" not in with_none


# 3. Examples are a style reference only — they must never replace or
# corrupt the structured tone/greeting/etc. fields already in the block.
def test_examples_are_additive_never_replace_style_fields():
    style = {"tone": "casual, warm, empathetic", "greeting_style": "first name, casual (Hey {name}!)",
             "closing_style": "Thanks!", "emoji_usage": "none", "sentence_length": "short",
             "paragraph_style": "short", "use_bullets": False, "use_customer_name": "always"}
    block = svc.build_style_prompt_block(style, ["Customer: hi\n\nLuna's reply: hello!"])
    assert "TONE: casual, warm, empathetic" in block
    assert "Customer: hi" in block


# 4. Tenant isolation at the exact point the live agent reads examples —
# Brand A's uploaded example must never appear when resolving Brand B's
# prompt, even when both have rows in the same (mocked) table.
def test_get_uploaded_example_snippets_is_scoped_per_brand():
    examples = {
        BRAND_A: [{"id": "ex-a", "content": "Customer: hiiii\n\nLuna's reply: helloooo whatsup honey?"}],
        BRAND_B: [{"id": "ex-b", "content": "Customer: hi\n\nLuna's reply: Good afternoon, how may I assist?"}],
    }

    with patch("src.services.reply_style_service.supabase_select", side_effect=_fake_select(examples)):
        snippets_a = svc.get_uploaded_example_snippets(BRAND_A)
        snippets_b = svc.get_uploaded_example_snippets(BRAND_B)

    assert snippets_a == ["Customer: hiiii\n\nLuna's reply: helloooo whatsup honey?"]
    assert snippets_b == ["Customer: hi\n\nLuna's reply: Good afternoon, how may I assist?"]
    assert "honey" not in " ".join(snippets_b)
    assert "assist" not in " ".join(snippets_a)


# 5. Independent of the learning system: examples reach the prompt via
# get_uploaded_example_snippets regardless of approved-reply count or
# reply_style_mode eligibility — this must never touch generate_learned_profile.
def test_example_snippets_available_independent_of_approved_reply_count():
    examples = {BRAND_A: [{"id": "ex-1", "content": "Customer: hiiii\n\nLuna's reply: helloooo whatsup honey?"}]}
    with patch("src.services.reply_style_service.supabase_select", side_effect=_fake_select(examples)) as mock_select:
        snippets = svc.get_uploaded_example_snippets(BRAND_A)

    assert snippets
    # Only ever touched the examples table — no brands/tickets lookup, i.e.
    # no dependency on approved-reply count or reply_style_mode at all.
    tables_queried = {c.args[0] for c in mock_select.call_args_list}
    assert tables_queried == {"reply_style_examples"}


# 6. End-to-end: the exact reported bug, run through the real support-agent
# generation path (process_customer_query), not just the two helper
# functions in isolation. Proves the example text actually reaches the
# system prompt handed to the model for a live ticket reply.
def _fake_ai_response(reply_body: str):
    msg = MagicMock()
    msg.content = json.dumps({"intent": "general_inquiry", "reply_body": reply_body, "risk_level": "low"})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def test_uploaded_example_reaches_the_system_prompt_via_process_customer_query():
    brand = {
        "id": BRAND_A, "name": "Test Store", "agent_name": "Luna", "email_signature": None,
        "reply_style_mode": "preset", "reply_style_preset": "warm_friendly", "reply_style_profile": None,
        "shopify_connected": False, "shopify_access_token": None, "aftership_api_key": None,
    }
    examples = [{"id": "ex-1", "content": "Customer: hiiii\n\nLuna's reply: helloooo whatsup honey?"}]

    def fake_select(table, params=None):
        if table == "brands":
            return [brand]
        if table == "reply_style_examples":
            return examples
        return []

    captured = {}

    async def _capture_completion(*, messages, **kwargs):
        captured["messages"] = messages
        return _fake_ai_response("Hey there! How's it going?"), "test_provider", "test_model", \
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 1, "attempts": 1}

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=_capture_completion)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))), \
         patch("src.lib.supabase_client.supabase_select", side_effect=fake_select), \
         patch("src.services.reply_style_service.supabase_select", side_effect=fake_select):
        run(customer_success_agent.process_customer_query(
            query="hiiii",
            customer_info={"name": "Sam", "email": "sam@example.com", "channel": "email"},
            tenant_id="tenant-1",
            store_id=BRAND_A,
            ticket_id="ticket-1",
        ))

    system_content = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "helloooo whatsup honey?" in system_content
    assert "hiiii" in system_content
