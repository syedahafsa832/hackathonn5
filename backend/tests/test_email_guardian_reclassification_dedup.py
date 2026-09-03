"""
Behind-the-scenes AI usage bug: email_guardian_service.evaluate() called the
AI classifier UNCONDITIONALLY on every invocation. Gmail's `after:` search
operator is date-level, not time-level, so a message that isn't blocked by
the cheap Layers 1-3 filters and never becomes a ticket (i.e. anything the
guardian quarantines or outright blocks) keeps reappearing in every ~15s
poll cycle for the rest of that calendar day - and got RE-CLASSIFIED BY A
FRESH AI CALL every single time.

The existing dedup inside _create_quarantine_record only ever prevented a
duplicate DATABASE ROW (see its own docstring: "one email, 458 duplicate
rows" - a prior, different bug already fixed). It runs AFTER
_classify_email() already spent the AI call, so it never stopped the
re-classification itself - confirmed live: the same gmail_message_id was
re-classified 5+ minutes after its quarantine row already existed.
Outright "blocked" decisions (as opposed to "quarantined") persisted
nothing at all, so they had zero protection whatsoever.

Fix: evaluate() now looks up a persisted decision for this gmail_message_id
FIRST, before calling the classifier at all, and reconstructs the result
from what's stored - and outright-blocked decisions are now persisted too.

They're persisted as status="pending", not "auto_blocked" - a later fix
(see the "always pending, never auto_blocked" comments in
email_guardian_service.py) found that "auto_blocked" excluded a message from
the merchant's quarantine review queue entirely, with no way to see or
recover a misclassified block. _result_from_existing_record still
understands a legacy "auto_blocked" row (test 2 below) for any that were
already persisted before that fix, but evaluate() itself no longer creates
new ones.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.email_guardian_service import EmailGuardianService  # noqa: E402


_DEFAULT_SETTINGS = {"support_only_mode": True, "confidence_threshold": 0.75, "auto_reply_enabled": True}


def _email(msg_id="msg-1"):
    return {"id": msg_id, "subject": "hi", "body": "some message", "sender_email": "someone@example.com"}


# ── 1. A message already quarantined is never re-classified ────────────────

@pytest.mark.asyncio
async def test_previously_quarantined_message_skips_reclassification():
    svc = EmailGuardianService()
    existing_row = {
        "id": "q-1", "status": "pending",
        "ai_classification": "customer_support", "ai_confidence": 0.4,
    }

    with patch.object(svc, "_load_settings", return_value=_DEFAULT_SETTINGS), \
         patch.object(svc, "_find_existing_decision", return_value=existing_row), \
         patch.object(svc, "_classify_email", new=AsyncMock()) as mock_classify:
        result = await svc.evaluate(_email(), "brand-1", brand_name="Acme")

    mock_classify.assert_not_called()
    assert result.decision == "quarantined"
    assert result.quarantine_id == "q-1"
    assert result.auto_reply_enabled is False


# ── 2. A message already outright-blocked is never re-classified ───────────

@pytest.mark.asyncio
async def test_previously_auto_blocked_message_skips_reclassification():
    svc = EmailGuardianService()
    existing_row = {
        "id": "q-2", "status": "auto_blocked",
        "ai_classification": "spam", "ai_confidence": 0.95,
    }

    with patch.object(svc, "_load_settings", return_value=_DEFAULT_SETTINGS), \
         patch.object(svc, "_find_existing_decision", return_value=existing_row), \
         patch.object(svc, "_classify_email", new=AsyncMock()) as mock_classify:
        result = await svc.evaluate(_email(), "brand-1", brand_name="Acme")

    mock_classify.assert_not_called()
    assert result.decision == "blocked"
    assert result.quarantine_id == "q-2"


# ── 3. A genuinely new message still classifies normally ───────────────────

@pytest.mark.asyncio
async def test_new_message_with_no_existing_decision_still_classifies():
    svc = EmailGuardianService()

    with patch.object(svc, "_load_settings", return_value=_DEFAULT_SETTINGS), \
         patch.object(svc, "_find_existing_decision", return_value=None), \
         patch.object(svc, "_classify_email", new=AsyncMock(return_value=("customer_support", 0.9, True))):
        result = await svc.evaluate(_email(), "brand-1", brand_name="Acme")

    assert result.decision == "allowed"
    assert result.classification == "customer_support"


# ── 4. Outright-blocked decisions persist a "pending" record, never a
#      silent/unrecoverable "auto_blocked" one ─────────────────────────────

@pytest.mark.asyncio
async def test_unrelated_high_confidence_block_persists_pending_not_auto_blocked():
    svc = EmailGuardianService()

    with patch.object(svc, "_load_settings", return_value=_DEFAULT_SETTINGS), \
         patch.object(svc, "_find_existing_decision", return_value=None), \
         patch.object(svc, "_classify_email", new=AsyncMock(return_value=("spam", 0.99, False))), \
         patch.object(svc, "_create_quarantine_record", return_value="q-new") as mock_create:
        result = await svc.evaluate(_email(), "brand-1", brand_name="Acme")

    # "quarantined", not "blocked" — this now goes into the same merchant-visible
    # review queue as a low-confidence email, not a silent dead end.
    assert result.decision == "quarantined"
    assert result.quarantine_id == "q-new"
    mock_create.assert_called_once()
    args, kwargs = mock_create.call_args
    # No explicit status arg passed at all — _create_quarantine_record's own
    # default ("pending") is what persists, never "auto_blocked".
    assert len(args) <= 4 and "status" not in kwargs


@pytest.mark.asyncio
async def test_blocked_ai_classification_persists_pending_not_auto_blocked():
    """The other outright-blocked path (support_only_mode + a classification
    in BLOCKED_CLASSIFICATIONS) - previously the one with zero persistence
    at all, never even a duplicate-row-safe insert."""
    from src.services.email_guardian_service import BLOCKED_CLASSIFICATIONS
    blocked_classification = next(iter(BLOCKED_CLASSIFICATIONS))
    svc = EmailGuardianService()

    with patch.object(svc, "_load_settings", return_value=_DEFAULT_SETTINGS), \
         patch.object(svc, "_find_existing_decision", return_value=None), \
         patch.object(svc, "_classify_email", new=AsyncMock(return_value=(blocked_classification, 0.9, True))), \
         patch.object(svc, "_create_quarantine_record", return_value="q-new-2") as mock_create:
        result = await svc.evaluate(_email(), "brand-1", brand_name="Acme")

    assert result.decision == "quarantined"
    assert result.quarantine_id == "q-new-2"
    mock_create.assert_called_once()
    args, kwargs = mock_create.call_args
    assert len(args) <= 4 and "status" not in kwargs


# ── 5. The lookup itself is scoped by brand_id + gmail_message_id ──────────

def test_find_existing_decision_queries_by_brand_and_message_id():
    svc = EmailGuardianService()
    with patch("src.services.email_guardian_service.supabase_select", return_value=[{"id": "q-3", "status": "pending"}]) as mock_select:
        result = svc._find_existing_decision("brand-1", "msg-42")

    assert result == {"id": "q-3", "status": "pending"}
    mock_select.assert_called_once_with("email_quarantine", {
        "brand_id": "eq.brand-1", "gmail_message_id": "eq.msg-42",
    })


def test_find_existing_decision_with_no_message_id_skips_the_query():
    svc = EmailGuardianService()
    with patch("src.services.email_guardian_service.supabase_select") as mock_select:
        result = svc._find_existing_decision("brand-1", None)

    assert result is None
    mock_select.assert_not_called()


def test_find_existing_decision_fails_open_on_lookup_error():
    """A Supabase hiccup must not permanently block real messages from ever
    being classified - degrade to 'nothing found', not an exception."""
    svc = EmailGuardianService()
    with patch("src.services.email_guardian_service.supabase_select", side_effect=RuntimeError("db down")):
        result = svc._find_existing_decision("brand-1", "msg-42")

    assert result is None
