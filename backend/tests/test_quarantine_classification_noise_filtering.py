"""
Quarantine Queue was showing obvious promotional/automated noise (a
Pinterest legal/marketing blast, a Shopify "you have received a refund"
notification) right alongside genuine ambiguous customer messages -
because email_guardian_service.py's "blocked classification" branch
(support_only_mode + classification in BLOCKED_CLASSIFICATIONS) had NO
confidence gate at all: any confidence, however low, went straight into
the merchant-visible "pending" queue. Only the separate "not relevant"
branch had a confidence gate.

That same missing gate is also what swallowed a real customer's "are you
AI? I want to talk to a human, not you" - classified spam at 0.98 - with
zero merchant visibility, back when un-gated meant "auto_blocked, never
shown" instead of "pending, always shown" (see
test_email_guardian_reclassification_dedup.py's history). Neither extreme
was right on its own.

Fix: the SAME confidence gate now applies uniformly to both noise
conditions (not relevant, or a blocked classification). A CONFIDENT noise
verdict (spam/promotion/newsletter/outreach/automation, or unrelated to
the brand) skips the queue (status="auto_blocked", never shown - a real
customer wouldn't send it). Anything below that confidence - including an
unusual one-off human message the classifier can't cleanly place - still
lands in "pending" for a person to review. No sender/domain blocklist
anywhere; the decision is made purely from the classifier's own
(classification, confidence, relevant) output.

These tests exercise evaluate() end-to-end (real routing logic, mocked
classifier output only) for the exact scenarios reported live.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402
from src.api.routes.v2_quarantine import router as quarantine_router  # noqa: E402
from src.services.email_guardian_service import EmailGuardianService  # noqa: E402

_DEFAULT_SETTINGS = {"support_only_mode": True, "confidence_threshold": 0.75, "auto_reply_enabled": True}


def _email(subject, body, msg_id="msg-1"):
    return {"id": msg_id, "subject": subject, "body": body, "sender_email": "someone@example.com"}


async def _evaluate(classify_result, subject="hi", body="body"):
    svc = EmailGuardianService()
    with patch.object(svc, "_load_settings", return_value=_DEFAULT_SETTINGS), \
         patch.object(svc, "_find_existing_decision", return_value=None), \
         patch.object(svc, "_classify_email", new=AsyncMock(return_value=classify_result)), \
         patch.object(svc, "_create_quarantine_record", return_value="q-1") as mock_create:
        result = await svc.evaluate(_email(subject, body), "brand-1", brand_name="Acme")
    return result, mock_create


# ── 1. Promotional email (Pinterest-style) → excluded from quarantine ──────

@pytest.mark.asyncio
async def test_promotional_email_does_not_enter_quarantine():
    # A confident "promotion" classification, not relevant to the brand as
    # a customer - exactly what a real classifier should return for a mass
    # legal/marketing blast.
    result, mock_create = await _evaluate(
        classify_result=("promotion", 0.98, False),
        subject="We're updating our Business Terms of Service and Privacy Policy",
        body="Pinterest 651 Brannan Street... Help Center... Unsubscribe...",
    )

    assert result.decision == "blocked"
    args, kwargs = mock_create.call_args
    assert (args[4] if len(args) > 4 else kwargs.get("status")) == "auto_blocked", (
        "a confident promotional classification must never reach the merchant's "
        "pending Quarantine Queue"
    )


# ── 2. Automated Shopify transactional notification → excluded ─────────────

@pytest.mark.asyncio
async def test_automated_shopify_notification_does_not_enter_quarantine():
    result, mock_create = await _evaluate(
        classify_result=("automation", 0.95, False),
        subject="Refund notification",
        body="You have received a refund for order #1004 from tresolv.",
    )

    assert result.decision == "blocked"
    args, kwargs = mock_create.call_args
    assert (args[4] if len(args) > 4 else kwargs.get("status")) == "auto_blocked", (
        "a confident automated-notification classification must never reach the "
        "merchant's pending Quarantine Queue"
    )


# ── 3. "Are you AI?" → still quarantine-able at low confidence ─────────────

@pytest.mark.asyncio
async def test_are_you_ai_can_still_enter_quarantine_at_low_confidence():
    # A real person's message the classifier can't confidently place - even
    # if it lands on a BLOCKED_CLASSIFICATIONS label, low confidence must
    # never silently disappear.
    result, mock_create = await _evaluate(
        classify_result=("customer_support", 0.4, True),
        subject="hi",
        body="are you ai? i want to talk to human not you",
    )

    assert result.decision == "quarantined"
    assert result.quarantine_id == "q-1"
    args, kwargs = mock_create.call_args
    assert len(args) <= 4 and "status" not in kwargs, (
        "an ambiguous human message must land in 'pending', not be silently "
        "auto_blocked or auto_allowed"
    )


@pytest.mark.asyncio
async def test_are_you_ai_misclassified_as_spam_at_low_confidence_still_quarantines():
    """Even if the classifier still mislabels it 'spam' (not customer_support),
    a LOW-confidence spam call must go to pending, not auto_blocked - this is
    the exact production incident that motivated the confidence gate."""
    result, mock_create = await _evaluate(
        classify_result=("spam", 0.5, True),
        subject="hi",
        body="are you ai? i want to talk to human not you",
    )

    assert result.decision == "quarantined"
    args, kwargs = mock_create.call_args
    assert len(args) <= 4 and "status" not in kwargs


# ── 4. "Who is the founder?" → still quarantine-able as uncertain support ──

@pytest.mark.asyncio
async def test_who_is_the_founder_can_still_enter_quarantine():
    result, mock_create = await _evaluate(
        classify_result=("customer_support", 0.35, True),
        subject="Question",
        body="Who is the founder?",
    )

    assert result.decision == "quarantined"
    assert result.reason == "low_confidence"
    args, kwargs = mock_create.call_args
    assert len(args) <= 4 and "status" not in kwargs


# ── 5. Normal customer-support email → unchanged existing behavior ─────────

@pytest.mark.asyncio
async def test_normal_support_question_is_allowed_through_unchanged():
    result, _ = await _evaluate(
        classify_result=("customer_support", 0.92, True),
        subject="Order question",
        body="Where is my order?",
    )

    assert result.decision == "allowed"
    assert result.classification == "customer_support"


# ── 6. Promote / Discard still work for a message quarantined this way ─────

def _quarantine_app():
    app = FastAPI()
    app.include_router(quarantine_router, prefix="/api/v1")
    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(tenant_id="tenant-1", email="agent@example.com")
    return app


def test_promote_still_works_for_an_ambiguous_human_quarantine_record():
    """A record created via the low-confidence noise path is just a normal
    'pending' row - promote must work exactly as it does for any other
    quarantined email, with no special-casing needed."""
    row = {
        "id": "q-ai-1", "brand_id": "brand-1", "status": "pending",
        "sender_email": "customer@example.com", "subject": "hi",
        "body_preview": "are you ai? i want to talk to human not you",
        "thread_id": "t-ai-1", "gmail_message_id": "msg-ai-1",
        "ai_classification": "customer_support", "ai_confidence": 0.4,
    }

    with patch("src.api.routes.v2_quarantine.supabase_select") as mock_select, \
         patch("src.api.routes.v2_quarantine.supabase_update", return_value={"id": "q-ai-1"}), \
         patch("src.api.routes.v2_quarantine._run_promotion", new=AsyncMock()):
        mock_select.side_effect = [
            [{"id": "brand-1", "tenant_id": "tenant-1", "is_active": True}],  # _get_brand_for_tenant
            [row],   # quarantine record lookup
            [],      # gmail_message_id dedup check against tickets
        ]
        client = TestClient(_quarantine_app())
        resp = client.post("/api/v1/quarantine/q-ai-1/promote")

    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True


def test_discard_still_works_for_a_confidently_promotional_quarantine_record():
    """Even a record the merchant chose to promote/discard manually (e.g.
    after flipping an old auto_blocked row back to pending) uses the exact
    same discard path as any other pending item."""
    row = {"id": "q-promo-1", "brand_id": "brand-1", "status": "pending"}

    with patch("src.api.routes.v2_quarantine.supabase_select") as mock_select, \
         patch("src.api.routes.v2_quarantine.supabase_update", return_value={"id": "q-promo-1"}) as mock_update:
        mock_select.side_effect = [
            [{"id": "brand-1", "tenant_id": "tenant-1", "is_active": True}],
            [row],
        ]
        client = TestClient(_quarantine_app())
        resp = client.post("/api/v1/quarantine/q-promo-1/discard")

    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True
    mock_update.assert_called_once()
