"""
Internal prompt/instruction text must never become a customer- or
merchant-visible conversation message.

Root cause: v2_chat_widget.py used to prepend a literal instruction string,
"[CHAT MODE — reply in 1-3 short sentences, conversational tone, no bullet
points]", directly onto the customer's message before calling
process_customer_query(). customer_success_agent.py used that same magic
string (`"[CHAT MODE" in query`) as its ONLY signal for "this is a chat
request" (_is_chat). return_actions_integration.py then persisted that same
`query` string verbatim into actions.original_message (message=query[:1000]
in _create_action), which dashboard/src/pages/Actions.jsx quotes directly
to the merchant on the Escalations page as if the customer had typed it.

Fixed by dropping the text-prefix approach entirely: chat formatting is now
driven by the already-set, real `customer_info["channel"] == "chat"` signal
(set by v2_chat_widget.py's own customer_info dict), injected into the
system prompt directly rather than mixed into message content that gets
persisted or pattern-matched elsewhere.
"""
import os
import sys
import inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.agent.customer_success_agent import customer_success_agent  # noqa: E402


def _prompt(channel):
    return customer_success_agent._construct_v3_prompt(
        customer_info={"name": "Jane", "email": "jane@example.com", "channel": channel},
        rag_context="", sizing_context="", tool_context="", action_context="",
    )


# ── The prompt builder gets the chat instruction from customer_info, never
#     from query text that could end up persisted elsewhere ─────────────────

def test_chat_channel_gets_short_reply_instruction_in_the_prompt():
    prompt = _prompt("chat")
    assert "short sentences" in prompt.lower()
    assert "no bullet points" in prompt.lower()


def test_email_channel_does_not_get_the_chat_formatting_instruction():
    prompt = _prompt("email")
    assert "live chat" not in prompt.lower()


def test_construct_v3_prompt_never_contains_the_leaked_instruction_string():
    """Defense in depth: even if something regresses, this literal string
    must never appear anywhere in the generated prompt for any channel -
    it's meant to be a real instruction to the model, not text the model
    could ever echo back or that could be confused with message content."""
    for channel in ("chat", "email", None):
        prompt = _prompt(channel)
        assert "[CHAT MODE" not in prompt


# ── The source files that used to construct/detect the leaking prefix no
#     longer do, at all — mechanical regression guard against reintroducing it ──

def test_chat_widget_route_no_longer_prefixes_the_query_with_an_instruction_string():
    import src.api.routes.v2_chat_widget as widget_mod
    assert "[CHAT MODE" not in inspect.getsource(widget_mod)


def test_test_luna_preview_route_no_longer_prefixes_the_query_with_an_instruction_string():
    import src.api.routes.v2_brands as brands_mod
    assert "[CHAT MODE" not in inspect.getsource(brands_mod)


def test_agent_detects_chat_mode_from_channel_field_not_from_query_text():
    """The one place _is_chat is computed must key off customer_info, never
    off a magic string that could appear in the query for other reasons
    (e.g. a customer literally pasting/quoting something that looks like
    an instruction)."""
    source = inspect.getsource(customer_success_agent.process_customer_query)
    assert '"[CHAT MODE" in query' not in source
    assert 'customer_info.get("channel") == "chat"' in source


# ── End-to-end: the real chat route builds `query` from the customer's
#     message alone - the thing that actually gets persisted as
#     actions.original_message and rendered to merchants ───────────────────

def test_chat_widget_generate_reply_builds_query_from_message_only():
    """_generate_reply is the one place v2_chat_widget.py builds `query`
    before handing it to process_customer_query (and, downstream,
    return_actions_integration._create_action's `message` param). Must be
    exactly the customer's own text (optionally with prior-turn history),
    never an instruction string mixed in."""
    import src.api.routes.v2_chat_widget as widget_mod
    source = inspect.getsource(widget_mod._generate_reply)
    assert "[CHAT MODE" not in source
    assert "query=full_query" in source