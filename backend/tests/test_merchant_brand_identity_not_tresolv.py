"""
Merchant identity leak: a customer asking "Who is the founder of this
company?" on the connected Shopify store "Syedahafsa1983's Store" got back
"The founder of tresolv is Syeda Hafsa herself, and she oversees every
aspect of the store..." - tResolv (the software powering this support
agent) answered for itself instead of the connected merchant, and invented
a claim ("oversees every aspect of the store") with no grounding at all.

Traced root cause: tenant/brand resolution and RAG retrieval are already
correctly scoped -
  - customer_success_agent.py's brand lookup is a single supabase_select on
    "brands" filtered by the exact store_id primary key (never by name/
    domain, which could collide) - see process_customer_query's "_b = _sel(
    'brands', {'id': f'eq.{store_id}'})".
  - brand_knowledge_service.get_brand_context(store_id, query) calls the
    match_brand_rag_chunks RPC with p_brand_id=store_id - tenant-scoped by
    design (see test_knowledge_base_brand_scoping.py).
  - rag_engine.py's older unscoped fallback has zero live callers (grepped;
    only mentioned in a comment warning not to reintroduce it) - not the
    mechanism here.
So "tresolv" was never injected by a code-level cross-tenant leak. The
actual gap: _construct_v3_prompt's KNOWLEDGE BASE grounding rule only ever
forbade inventing PRODUCT claims ("material, fit, texture, quality,
popularity, durability, price, availability, marketing claims") - it said
nothing about company/brand-identity questions (founder, owner, "about the
company"). With no rule covering that category, the model was free to
answer such a question from its own general knowledge (which, for the word
"tresolv" - the very name of the real product this codebase implements -
it evidently has real associations for) instead of grounding on the
tenant's own KB or refusing to guess.

Fix: _construct_v3_prompt now has an explicit COMPANY/BRAND IDENTITY
QUESTIONS rule - answer only from the tenant's own KNOWLEDGE BASE, never
substitute tResolv's own identity, and reuse the exact same "don't have
that confirmed, team will follow up" fallback wording the KB rule already
established for ungrounded policy questions - no new escalation mechanism.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.agent.customer_success_agent import customer_success_agent  # noqa: E402


def _prompt(brand_name, rag_context="", agent_name="Luna"):
    return customer_success_agent._construct_v3_prompt(
        customer_info={"name": "Jane", "email": "jane@example.com", "channel": "email"},
        rag_context=rag_context, sizing_context="", tool_context="", action_context="",
        brand_name=brand_name, agent_name=agent_name,
    )


# ── 1 & 4. Two different merchants each get their OWN brand identity ────────

def test_prompt_identity_line_uses_the_current_merchants_own_name():
    prompt = _prompt("Hafsa Clothing")
    assert "the AI customer support employee for Hafsa Clothing" in prompt


def test_two_different_merchants_get_two_different_identity_lines():
    prompt_a = _prompt("Hafsa Clothing")
    prompt_b = _prompt("Aurelio & Finch")
    assert "Hafsa Clothing" in prompt_a and "Aurelio & Finch" not in prompt_a
    assert "Aurelio & Finch" in prompt_b and "Hafsa Clothing" not in prompt_b


# ── 2. tResolv's own identity is never a valid substitute ───────────────────

def test_prompt_explicitly_forbids_answering_as_tresolv():
    prompt = _prompt("Syedahafsa1983's Store")
    lower = prompt.lower()
    assert "company/brand identity" in lower
    # The word "tresolv" is only allowed to appear as part of the explicit
    # prohibition, naming the connected store as the one and only identity.
    assert "tresolv" in lower
    assert "never allowed to answer using tresolv" in lower or "never the answer" in lower
    assert "you work for syedahafsa1983's store only" in lower


def test_reported_bug_wording_is_named_as_forbidden_not_produced():
    """The prompt itself must never contain the literal fabricated claim
    from the reported bug - it should only ever appear (if at all) inside
    an instruction telling the model NOT to say it."""
    prompt = _prompt("Syedahafsa1983's Store")
    assert "oversees every aspect of the store" not in prompt.lower()


# ── 3. No grounding for founder/owner info -> must not invent, must reuse
#     the existing "don't have that confirmed" fallback, never guess ───────

def test_no_kb_match_instructs_the_model_not_to_guess_company_identity():
    prompt = _prompt("Hafsa Clothing", rag_context="")
    lower = prompt.lower()
    assert "do not guess from general knowledge" in lower
    # Reuses the SAME fallback convention already defined for ungrounded
    # policy questions above it in the prompt - not a new workflow.
    assert "don't have that confirmed" in lower


def test_kb_containing_founder_info_is_the_authoritative_source():
    """When the tenant's OWN knowledge base genuinely has founder/ownership
    info, that real content still reaches the prompt as before - the fix
    only closes the ungrounded-guessing gap, it doesn't hide real KB data."""
    prompt = _prompt("Hafsa Clothing", rag_context="Founder: Jane Doe, started the brand in 2019.")
    assert "Founder: Jane Doe, started the brand in 2019." in prompt


# ── 5. No cross-tenant KB bleed at the prompt-construction level ───────────

def test_one_brands_rag_context_never_appears_in_another_brands_prompt():
    prompt_a = _prompt("Hafsa Clothing", rag_context="Hafsa Clothing was founded by Syeda Hafsa in 2023.")
    prompt_b = _prompt("Aurelio & Finch", rag_context="Aurelio & Finch was founded by A. Finch in 2015.")
    assert "Syeda Hafsa" in prompt_a and "Syeda Hafsa" not in prompt_b
    assert "A. Finch" in prompt_b and "A. Finch" not in prompt_a


# ── 6. Existing order/support question behavior is unaffected ──────────────

def test_order_data_present_still_gets_the_live_data_instruction_unchanged():
    prompt = customer_success_agent._construct_v3_prompt(
        customer_info={"name": "Jane", "email": "jane@example.com", "channel": "email"},
        rag_context="", sizing_context="", tool_context="Order #1001: Essential Hoodie, $45.00",
        action_context="", brand_name="Hafsa Clothing",
    )
    assert "LIVE DATA FROM SHOPIFY" in prompt
    assert "Order #1001: Essential Hoodie, $45.00" in prompt


def test_no_order_data_still_gets_the_ask_for_order_number_instruction_unchanged():
    prompt = _prompt("Hafsa Clothing")
    assert "No order data fetched" in prompt
