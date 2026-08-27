"""
"Review Luna's Work" — POST /api/tickets/{id}/review and
GET /api/tickets/review/queue (tickets.py).

Reuses the existing tickets table and the exact fields Reply Style
Learning's organic counter already reads (human_approved / human_response),
plus a new human_rejected field (migration 049) for the one review outcome
that had no prior representation. review_status (Needs Review / Approved /
Edited / Rejected) is a pure computation over those fields, not a second
status column.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import tickets as tickets_module  # noqa: E402
from src.api.routes.tickets import _compute_review_status, _ticket_has_luna_reply  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402
from src.services import reply_style_service  # noqa: E402

app = FastAPI()
app.include_router(tickets_module.router, prefix="/api")
client = TestClient(app)

TICKET_ID = "ticket-1"
BRAND_ID = "brand-1"
TENANT_ID = "tenant-1"


def _override_tenant(tenant_id=TENANT_ID):
    async def _dep():
        return TenantContext(tenant_id=tenant_id, email="merchant@example.com")
    return _dep


def _with_tenant(fn, tenant_id=TENANT_ID):
    app.dependency_overrides[get_current_tenant] = _override_tenant(tenant_id)
    try:
        return fn()
    finally:
        app.dependency_overrides.clear()


def _ticket(**overrides):
    t = {
        "id": TICKET_ID, "brand_id": BRAND_ID, "store_id": BRAND_ID,
        "ai_reply": "We can help with that!", "ai_draft": None,
        "human_approved": False, "human_response": None, "human_rejected": False,
        "messages": [],
    }
    t.update(overrides)
    return t


def _select_honoring_or_filter(rows):
    """A mock supabase_select that actually applies reply_style_service's
    '(human_approved.is.true,human_response.not.is.null)' filter server-side,
    the way real Postgres would — a naive return_value=[...] would include
    rows that shouldn't match, silently making the learning-count assertions
    below meaningless."""
    def fn(table, params=None):
        params = params or {}
        if "or" in params:
            return [r for r in rows if r.get("human_approved") or r.get("human_response")]
        return rows
    return fn


def _post_review(payload, ticket=None):
    ticket = ticket or _ticket()
    with patch("src.api.routes.tickets._assert_ticket_access", new=AsyncMock(return_value=ticket)), \
         patch("src.api.routes.tickets.supabase_update") as mock_update:
        resp = _with_tenant(lambda: client.post(f"/api/tickets/{TICKET_ID}/review", json=payload))
    return resp, mock_update


# ── _compute_review_status: pure derivation, no DB writes ──────────────────

def test_compute_review_status_transitions():
    assert _compute_review_status({"ai_reply": None, "ai_draft": None}) is None
    assert _compute_review_status({"ai_reply": "hi"}) == "needs_review"
    assert _compute_review_status({"ai_reply": "hi", "human_approved": True}) == "approved"
    assert _compute_review_status({"ai_reply": "hi", "human_approved": True, "human_response": "x"}) == "edited"
    assert _compute_review_status({"ai_reply": "hi", "human_rejected": True}) == "rejected"
    # Rejected always wins, even over a stale approved flag from an earlier state.
    assert _compute_review_status({"ai_reply": "hi", "human_approved": True, "human_rejected": True}) == "rejected"


# ── _ticket_has_luna_reply: the Train Luna "AI conversations" undercount fix ──
# An auto-resolved chat-widget conversation never populates ai_reply/
# ai_draft/ai_response - its reply lives only in `messages`. Real production
# data confirmed the exact message shapes each writer uses (see the
# function's own docstring); these cover every one of them.

def test_ticket_has_luna_reply_true_when_scalar_columns_set():
    # Anything _compute_review_status() already recognizes must still count.
    assert _ticket_has_luna_reply({"ai_reply": "hi", "messages": []}) is True
    assert _ticket_has_luna_reply({"ai_draft": "hi", "messages": []}) is True


def test_ticket_has_luna_reply_true_for_chat_widget_message_shape():
    # v2_chat_widget.py's shape: role="ai", no ai_reply/ai_draft set at all.
    ticket = {
        "ai_reply": None, "ai_draft": None,
        "messages": [
            {"direction": "inbound", "body": "where is my order"},
            {"direction": "outbound", "body": "Let me check that for you.", "role": "ai"},
        ],
    }
    assert _ticket_has_luna_reply(ticket) is True


def test_ticket_has_luna_reply_true_for_gmail_auto_reply_message_shape():
    # message_processor.py's shape: from="AI Agent" + role="assistant".
    ticket = {
        "ai_reply": None, "ai_draft": None,
        "messages": [{"from": "AI Agent", "role": "assistant", "body": "Handled automatically."}],
    }
    assert _ticket_has_luna_reply(ticket) is True


def test_ticket_has_luna_reply_true_for_post_execution_confirmation_message_shape():
    # actions_service._post_execution_notify's shape: from="AI Agent" only, no role key.
    ticket = {
        "ai_reply": None, "ai_draft": None,
        "messages": [{"from": "AI Agent", "body": "Your cancellation has been processed."}],
    }
    assert _ticket_has_luna_reply(ticket) is True


def test_ticket_has_luna_reply_false_for_customer_only_messages():
    ticket = {
        "ai_reply": None, "ai_draft": None,
        "messages": [{"direction": "inbound", "body": "hello?"}],
    }
    assert _ticket_has_luna_reply(ticket) is False


def test_ticket_has_luna_reply_false_with_no_messages_and_no_scalar_reply():
    assert _ticket_has_luna_reply({"ai_reply": None, "ai_draft": None, "messages": []}) is False
    assert _ticket_has_luna_reply({"ai_reply": None, "ai_draft": None}) is False


def test_ticket_has_luna_reply_never_widens_compute_review_status_itself():
    # Critical: a messages-only AI reply must NOT make _compute_review_status
    # (the Review Luna's Work queue) start showing this ticket as
    # needing/awaiting review - it already auto-resolved with no human step.
    ticket = {
        "ai_reply": None, "ai_draft": None,
        "messages": [{"direction": "outbound", "body": "Handled.", "role": "ai"}],
    }
    assert _ticket_has_luna_reply(ticket) is True
    assert _compute_review_status(ticket) is None


# 2. Real approved reply increments organic learning count
def test_approve_sets_human_approved_and_counts_toward_reply_style_learning():
    resp, mock_update = _post_review({"decision": "approve"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["review_status"] == "approved"

    updates = mock_update.call_args.args[2]
    assert updates["human_approved"] is True
    assert updates["human_rejected"] is False

    updated_ticket = {**_ticket(), **updates}
    with patch("src.services.reply_style_service.supabase_select", side_effect=_select_honoring_or_filter([updated_ticket])):
        assert reply_style_service.count_eligible_approved_replies(BRAND_ID) == 1


# 3. Edited-and-approved reply increments organic learning count
def test_edit_approve_sets_human_response_and_counts_toward_reply_style_learning():
    resp, mock_update = _post_review({"decision": "edit_approve", "edited_response": "Here's the corrected reply."})
    assert resp.status_code == 200, resp.text
    assert resp.json()["review_status"] == "edited"

    updates = mock_update.call_args.args[2]
    assert updates["human_response"] == "Here's the corrected reply."
    assert updates["human_approved"] is True

    updated_ticket = {**_ticket(), **updates}
    with patch("src.services.reply_style_service.supabase_select", side_effect=_select_honoring_or_filter([updated_ticket])):
        assert reply_style_service.count_eligible_approved_replies(BRAND_ID) == 1


def test_edit_approve_requires_edited_response_text():
    resp, _ = _post_review({"decision": "edit_approve"})
    assert resp.status_code == 400


# 4. Rejected reply does not count as approved
def test_reject_does_not_set_approved_fields_or_count_toward_learning():
    resp, mock_update = _post_review({"decision": "reject", "rejection_reason": "Wrong tone"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["review_status"] == "rejected"

    updates = mock_update.call_args.args[2]
    assert updates["human_rejected"] is True
    assert updates["human_rejected_reason"] == "Wrong tone"
    assert "human_approved" not in updates
    assert "human_response" not in updates

    updated_ticket = {**_ticket(), **updates}
    with patch("src.services.reply_style_service.supabase_select", side_effect=_select_honoring_or_filter([updated_ticket])):
        assert reply_style_service.count_eligible_approved_replies(BRAND_ID) == 0


def test_review_response_contains_exactly_what_the_frontend_needs_for_an_instant_update():
    """Regression guard for the "Approve feels slow/does nothing" fix:
    dashboard/src/hooks/useApi.js's useSubmitTicketReview() now patches its
    own review-queue cache straight from this response instead of waiting
    on a second, slower GET /review/queue round trip (measured ~1.5s+
    against live Supabase) before the item's badge visibly changes. That
    only works if this response reliably carries `success` and
    `review_status` with no dependency on a second request - never
    silently drop or rename these without also updating that hook."""
    resp, _ = _post_review({"decision": "approve"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["review_status"] == "approved"


def test_review_requires_an_existing_luna_reply():
    ticket = _ticket(ai_reply=None, ai_draft=None)
    resp, _ = _post_review({"decision": "approve"}, ticket=ticket)
    assert resp.status_code == 400


# 7. Approval/edit/rejection is correctly associated with the brand
def test_review_rejects_ticket_owned_by_a_different_tenant():
    ticket = _ticket(brand_id="brand-OTHER", store_id="brand-OTHER")
    with patch("src.api.routes.tickets.supabase_service.get_ticket_by_id", new=AsyncMock(return_value=ticket)), \
         patch("src.api.routes.tickets._get_tenant_brand_ids", new=AsyncMock(return_value=[BRAND_ID])):
        resp = _with_tenant(lambda: client.post(f"/api/tickets/{TICKET_ID}/review", json={"decision": "approve"}))
    assert resp.status_code == 404


# ── Review queue list ───────────────────────────────────────────────────────

def test_review_queue_computes_status_and_excludes_tickets_with_no_ai_reply():
    tickets = [
        _ticket(id="t-needs"),
        _ticket(id="t-approved", human_approved=True),
        _ticket(id="t-edited", human_approved=True, human_response="edited text"),
        _ticket(id="t-rejected", human_rejected=True),
        _ticket(id="t-no-ai", ai_reply=None, ai_draft=None),
    ]
    with patch("src.api.routes.tickets._get_tenant_brand_ids", new=AsyncMock(return_value=[BRAND_ID])), \
         patch("src.services.supabase_service.supabase_service.get_tickets", new=AsyncMock(return_value=tickets)), \
         patch("src.api.routes.tickets.supabase_select", return_value=[]):
        resp = _with_tenant(lambda: client.get("/api/tickets/review/queue"))

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    status_by_id = {i["ticket_id"]: i["review_status"] for i in items}
    assert status_by_id == {
        "t-needs": "needs_review", "t-approved": "approved",
        "t-edited": "edited", "t-rejected": "rejected",
    }
    assert "t-no-ai" not in status_by_id


def test_review_queue_filters_by_review_status():
    tickets = [_ticket(id="t-approved", human_approved=True), _ticket(id="t-needs")]
    with patch("src.api.routes.tickets._get_tenant_brand_ids", new=AsyncMock(return_value=[BRAND_ID])), \
         patch("src.services.supabase_service.supabase_service.get_tickets", new=AsyncMock(return_value=tickets)), \
         patch("src.api.routes.tickets.supabase_select", return_value=[]):
        resp = _with_tenant(lambda: client.get("/api/tickets/review/queue", params={"review_status": "approved"}))

    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["ticket_id"] == "t-approved"


# 11. Review queue exposes only what's necessary — not the full ticket row
def test_review_queue_never_returns_customer_email_field():
    tickets = [_ticket(id="t-needs", customer_email="realcustomer@example.com")]
    with patch("src.api.routes.tickets._get_tenant_brand_ids", new=AsyncMock(return_value=[BRAND_ID])), \
         patch("src.services.supabase_service.supabase_service.get_tickets", new=AsyncMock(return_value=tickets)), \
         patch("src.api.routes.tickets.supabase_select", return_value=[]):
        resp = _with_tenant(lambda: client.get("/api/tickets/review/queue"))

    item = resp.json()["items"][0]
    assert "customer_email" not in item


# 10. Brand A's tickets never appear in Brand B's (tenant B's) review queue
def test_review_queue_is_brand_scoped():
    with patch("src.api.routes.tickets._get_tenant_brand_ids", new=AsyncMock(return_value=["brand-B"])) as mock_brands, \
         patch("src.services.supabase_service.supabase_service.get_tickets", new=AsyncMock(return_value=[])) as mock_get_tickets, \
         patch("src.api.routes.tickets.supabase_select", return_value=[]):
        resp = _with_tenant(lambda: client.get("/api/tickets/review/queue", params={"store_id": BRAND_ID}), tenant_id="tenant-B")

    # store_id (brand-1) not in tenant B's own brands (brand-B) -> empty, no cross-brand leak
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "count": 0}
    mock_get_tickets.assert_not_called()
