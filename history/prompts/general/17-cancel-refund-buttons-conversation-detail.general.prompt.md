---
id: 17
title: Wire Cancel/Refund Buttons in Conversation Detail
stage: general
date: 2026-06-03
surface: agent
model: claude-sonnet-4-6
feature: none
branch: 006-email-guardian
user: Syeda Hafsa
command: Fix Cancel/Refund buttons in conversation detail — execute Shopify action + send confirmation email + resolve ticket
labels: ["backend","frontend","shopify","cancel","refund","conversation-detail","v2-tickets"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/api/routes/v2_tickets.py
  - ai-ops-console/src/pages/TicketDetail.jsx
  - backend/src/workers/message_processor.py
tests:
  - manual: open ticket with order #1005 → click Cancel → confirm dialog → button shows "Cancelling..." → success message → page reloads → ticket status=Resolved → customer receives email
  - manual: open ticket with paid order → click Refund → confirm → success message with amount → customer receives refund confirmation email
  - manual: click "Approve & Send" on AI draft → email sent via brand Gmail → conversation replay shows outbound message → ticket status=Resolved
  - manual: Address Change / Reship still shows "✓ Staged" (queued for approval, not direct-executed)
---

## Prompt

Full spec paste requesting:
1. Wire Cancel/Refund buttons to directly execute Shopify actions (not stage for approval)
2. Send confirmation email to customer after each action
3. Resolve ticket + append confirmation to conversation replay
4. Fix "Approve & Send" for AI drafts to also append to messages array

## Response snapshot

### Root cause analysis

**Previous flow**: `stageAction()` → `POST /api/v1/actions/create` → creates pending action record → NOTHING ELSE. The approval only happened from the separate Escalations page.

**Required flow**: Click Cancel/Refund → execute Shopify immediately → send email → resolve ticket.

### Backend changes — `v2_tickets.py`

Added two new endpoints before the `/{ticket_id}/escalate` route:

**`POST /api/v2/tickets/{ticket_id}/actions/cancel`**
1. Auth: `require_agent_or_admin`, brand ownership check
2. Validates Shopify connected on brand
3. Gets `detected_order_id` or `detected_order_number` from ticket
4. Calls `ShopifyClient.cancel_order(order_id, reason="customer", email_customer=False, restock=True)`
5. Builds confirmation email body (plain text, no HTML)
6. Sends via `brand_gmail_service.send_email()` — fails gracefully, notes in response
7. Appends message to `ticket.messages` array (`direction: "outbound"` if sent, else `"draft"`)
8. Sets `ticket.status = "resolved"`
9. Marks any pending `cancel_order` action for this ticket as `"executed"`

**`POST /api/v2/tickets/{ticket_id}/actions/refund`**
Same pattern but calls `ShopifyClient.process_refund(amount=None)` (full refund), fetches currency via `fetch_shopify_order`.

**`approve-ai` fix**: Now also appends sent message to `ticket.messages` as `direction: "outbound"` so the conversation replay shows it without a page reload.

### Frontend changes — `TicketDetail.jsx` `OrderPanel`

- Added `actionLoading` and `actionResult` state
- New `executeAction(endpoint, loadingKey, successMsg)` helper:
  - Shows `window.confirm()` before destructive actions
  - POSTs to the new backend endpoint
  - Shows success/error banner inside the panel
  - `window.location.reload()` after 1.8s on success (refreshes status badge + conversation)
- CANCEL and REFUND buttons now call `executeAction` pointing at v2 endpoints
- ADDRESS_CHANGE and RESHIP still use the old `stageAction` flow (pending approval queue)
- `handleApproveAI` now points at `/api/v2/tickets/{id}/approve-ai` and reloads on success

### Decision: window.location.reload() vs React Query invalidation

Used `window.location.reload()` — brutal but reliable. The alternative (invalidating `useTicket` and `useMessages` queries) would require passing `queryClient.invalidateQueries` into `OrderPanel` which complicates the component signature. Since this is a low-frequency action (one per ticket resolution), the reload UX is acceptable.

## Outcome

- ✅ Cancel button: executes Shopify cancellation + sends email + resolves ticket
- ✅ Refund button: executes full Shopify refund + sends email + resolves ticket
- ✅ Approve & Send: sends AI draft via Gmail + appends to messages array
- ✅ Confirmation email gracefully degrades if Gmail not connected (still resolves ticket, shows warning)
- ✅ Address Change / Reship: unchanged (still stage → approval queue)
- 🧪 Tests: Manual — see test checklist above
- 📁 Files: 2 files modified (v2_tickets.py, TicketDetail.jsx)

## Evaluation notes (flywheel)

- Risk: `ShopifyClient.cancel_order` raises `ShopifyError` exceptions (not just returning `{"success": False}`). The try/except wraps it and converts to 422 HTTP.
- Risk: If the brand's `shopify_connected` flag is True but token is expired, Shopify will return 401 — this surfaces as a 422 with Shopify's error message, giving the user actionable feedback.
- Next: Address Change endpoint (needs new address input form in frontend — out of scope for this prompt)
