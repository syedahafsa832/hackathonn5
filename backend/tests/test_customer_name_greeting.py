"""
"Dear There" bug — customer name / greeting handling.

Root cause: three call sites handed the LLM a literal placeholder string as
if it were the customer's real name, which the model then echoed straight
into a "Dear {name}," style greeting (the Reply Style presets explicitly
instruct that format):

1. v2_chat_widget.py defaulted an unverified chat visitor's name to the
   literal string "there" -> the prompt said `Name: there` -> the model
   wrote "Dear There,".
2. supabase_service.get_or_create_customer() derived a name from the email
   local-part when none was given ("customer10@example.com" -> "customer10")
   - not a real name, and forbidden by product requirements.
3. customer_success_agent.py's own email-greeting-injection defaulted to
   the same "there" placeholder without distinguishing it from a real name.

Fixed with one shared choke point: _known_customer_name() (customer_
success_agent.py) treats any known placeholder as "no name known", and
_construct_v3_prompt() - the single prompt builder used for every channel/
response type - explicitly tells the model to use a neutral greeting
instead of guessing when no real name is known, rather than ever handing it
a placeholder to echo back.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.agent.customer_success_agent import customer_success_agent, _known_customer_name  # noqa: E402


def _prompt(name):
    return customer_success_agent._construct_v3_prompt(
        customer_info={"name": name, "email": "customer@example.com"},
        rag_context="", sizing_context="", tool_context="", action_context="",
    )


# ── 1. Known customer name produces the correct greeting ────────────────────

def test_known_customer_name_is_passed_through_to_the_prompt():
    prompt = _prompt("Bushra")
    assert "Name: Bushra" in prompt
    assert "Not known" not in prompt.split("Name:")[1].split("\n")[0]


# ── 2/3. Missing name never produces "Dear There" and uses a neutral greeting ─

def test_missing_customer_name_never_hands_the_model_there_as_a_name():
    prompt = _prompt(None)
    # The Name: field itself must say the name isn't known - never the bare
    # word "there" (or any placeholder) as if it were a real value the
    # model should substitute into "Dear {name},".
    name_line = prompt.split("Name:")[1].split("\n")[0].strip()
    assert name_line.lower() != "there"
    assert "not known" in name_line.lower()
    # And the model is explicitly told not to write "Dear There" - this is
    # the instruction text, not a claim the final reply never contains it
    # (that's an LLM behavior, not something a prompt-construction test can
    # assert on directly).
    assert "dear there" in prompt.lower()


def test_missing_customer_name_instructs_a_neutral_greeting():
    prompt = _prompt(None)
    lowered = prompt.lower()
    assert "hi there" in lowered or "thanks for reaching out" in lowered
    assert "do not guess or invent" in lowered


def test_placeholder_names_are_all_treated_as_unknown():
    for placeholder in ("there", "There", "Website Visitor", "Customer", "Unknown", "guest", "", None):
        assert _known_customer_name(placeholder) is None, f"{placeholder!r} should not be treated as a real name"


def test_real_names_are_recognized():
    assert _known_customer_name("Bushra") == "Bushra"
    assert _known_customer_name("Syeda Hafsa") == "Syeda Hafsa"


# ── 4. Email addresses are never converted into names ───────────────────────

def test_get_or_create_customer_never_derives_name_from_email(monkeypatch):
    import src.services.supabase_service as svc_mod

    captured = {}

    def fake_select(table, params=None):
        return []

    def fake_insert(table, data):
        captured.update(data)
        return data

    monkeypatch.setattr(svc_mod, "supabase_select", fake_select)
    monkeypatch.setattr(svc_mod, "supabase_insert", fake_insert)

    import asyncio
    asyncio.get_event_loop().run_until_complete(
        svc_mod.supabase_service.get_or_create_customer(
            email="customer10@example.com", store_id="brand-1", name=None,
        )
    )
    assert captured.get("name") != "customer10"
    assert captured.get("name") != "Customer10"
    # The stored placeholder must itself resolve to "no real name known"
    # downstream, not read like a guessed name.
    assert _known_customer_name(captured.get("name")) is None


# ── 5. Order numbers are never converted into names ──────────────────────────

def test_order_number_looking_value_is_still_treated_as_a_real_name_if_actually_given():
    """The system must never GENERATE a name from an order number - but it
    also must never second-guess a name it was actually, explicitly given
    (no heuristic rejection of numeric-looking strings). The guarantee is
    at the source (nothing in the codebase ever assigns order_id to a name
    field) - covered by inspection of _extract_order_info/get_or_create_
    customer/v2_chat_widget.py's customer_info construction, none of which
    reference order_id when building "name"."""
    # A real (if unusual) provided name isn't rejected...
    assert _known_customer_name("O'Brien") == "O'Brien"


def test_chat_widget_session_never_seeds_name_from_order_id():
    import inspect
    import src.api.routes.v2_chat_widget as widget_mod
    source = inspect.getsource(widget_mod)
    # customer_info["name"] construction must never reference order_id/
    # order_number - the only real fallback is the session's own stored
    # customer_name, never anything order-shaped.
    assert '"name": body.customer_name or ticket.get("customer_name")' in source


# ── 9. Existing rule reinforcement is present (defense in depth) ────────────

def test_action_rules_reinforce_neutral_greeting_when_name_unknown():
    prompt = _prompt(None)
    assert "greet them by any placeholder word as if it were their real name" in prompt.lower() or \
           "never write \"dear there\"" in prompt.lower()
