"""
Order Inquiry mislabeled as Address Change (Task 2, item 1).

Root cause: _keyword_fallback()'s address-change trigger was the bare
substring 'address' - so any order-status/inquiry message that merely
mentions the word ("what address is this going to?", "can you confirm my
shipping address on order 1013?") matched and got classified (and, further
downstream, staged/displayed) as address_change, even though the customer
never asked to change anything. Every other fragment list in this file has
a guard against this class of false positive (_POLICY_QUESTION_FRAGS is
checked first specifically so a cancellation POLICY question is never
misread as a cancel action) - address never had an equivalent guard.

Fix: _ADDRESS_FRAGS now requires a change-shaped phrase ("change my
address", "update my address", "wrong address", "new address", "ship it
to", etc.) instead of the bare word. The LLM-path prompt's address_change
description was also tightened with an explicit negative example, mirroring
the existing "asking ABOUT a policy is never an action" guard for
cancellation.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.intent_detector import _keyword_fallback, INTENT_PROMPT  # noqa: E402


# ── The exact reported bug: an inquiry mentioning "address" ───────────────

def test_asking_what_address_is_on_file_is_not_address_change():
    result = _keyword_fallback("what address is my order #1013 going to?")
    assert result.action_type == "none"


def test_confirming_shipping_address_is_not_address_change():
    result = _keyword_fallback("can you confirm my shipping address on this order?")
    assert result.action_type == "none"


def test_asking_about_delivery_address_is_not_address_change():
    result = _keyword_fallback("is the delivery address correct for order 1013?")
    assert result.action_type == "none"


# ── Genuine address-change requests must still be caught ──────────────────

def test_explicit_change_my_address_is_still_address_change():
    result = _keyword_fallback("I need to change my address for order 1013")
    assert result.action_type == "address_change"


def test_wrong_address_is_still_address_change():
    result = _keyword_fallback("I put in the wrong address, can you fix it?")
    assert result.action_type == "address_change"


def test_new_address_with_change_verb_still_captures_raw_address():
    result = _keyword_fallback("please update my address to 123 Main Street, Lahore")
    assert result.action_type == "address_change"
    assert result.raw_address and "123 Main Street" in result.raw_address


def test_ship_to_a_different_address_is_still_address_change():
    result = _keyword_fallback("can you ship this to 45 Oak Ave instead")
    assert result.action_type == "address_change"


# ── Other action types checked for the same class of bug (per task ask) ───

def test_where_is_my_order_is_none_not_misrouted():
    result = _keyword_fallback("where is my order #1013?")
    assert result.action_type == "none"


def test_order_status_question_mentioning_cancel_word_is_still_guarded():
    """Regression guard: the existing cancellation policy-question guard
    (checked before _ADDRESS_FRAGS in the same function) must be unaffected
    by this change."""
    result = _keyword_fallback("why was my order cancelled?")
    assert result.action_type == "none"


# ── LLM-path prompt carries the same guard ─────────────────────────────────

def test_llm_prompt_explicitly_guards_against_inquiry_mentioning_address():
    lowered = INTENT_PROMPT.lower()
    assert "merely asking what address is on file" in lowered
    assert "explicitly want" in lowered
