---
id: 18
title: Auto-Send Acknowledgment + Wire Cancel/Refund Buttons
stage: general
date: 2026-06-03
surface: agent
model: claude-sonnet-4-6
feature: none
branch: 006-email-guardian
user: Syeda Hafsa
command: Fix AI not auto-sending acknowledgment for escalated tickets; wire Cancel/Refund buttons to execute directly in Shopify
labels: ["backend","frontend","message-processor","cancel","refund","acknowledgment","escalation"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/workers/message_processor.py
  - ai-ops-console/src/pages/TicketDetail.jsx
tests:
  - manual: send "cancel my order 1005" → wait 90s → customer Gmail should receive acknowledgment automatically → Escalations page shows pending cancel action
  - manual: open conversation detail → click Cancel → "Cancelling..." → success message → page reloads → customer receives confirmation → ticket = Resolved
  - manual: open conversation detail → click Refund → same flow
  - manual: click Update Address → shows "Queued" + "Go to Escalations to approve"
  - manual: Escalations approve still executes in Shopify + sends confirmation email (via _post_execution_notify)
---

## Prompt

Two focused fixes:
1. AI acknowledgment ("I've forwarded your cancellation request to our team") should auto-send even when ticket is escalated. Currently blocked by risk:medium check.
2. Cancel/Refund buttons in conversation detail should execute directly (Shopify + email). Update Address/Reship stay as stage-for-approval.

## Response snapshot

### Fix 1 — message_processor.py Stage 8

**Root cause**: The `else` branch (medium/high risk) set `should_auto_reply = False` and stored reply as `ai_draft`. This meant the acknowledgment email was never sent automatically.

**Key insight**: There are two separate things:
- The acknowledgment reply ("we'll review your cancellation") — safe to always send
- The Shopify action execution (cancel/refund) — requires human approval

**Change**: In the `else` branch, if `confidence >= 0.5` and there's a reply body, set `should_auto_reply = True` and store as `ai_reply` (not `ai_draft`). Ticket status stays `escalated` so the action still needs approval in Escalations.

```python
else:
    ticket_payload["status"] = "escalated"
    if reply_body and confidence >= 0.5:
        should_auto_reply = True
        ticket_payload["ai_reply"] = reply_body
        logger.info(f"[PROCESSOR] ✓ Sending acknowledgment (escalated)")
    else:
        ticket_payload["ai_draft"] = reply_body
```

### Fix 2 — TicketDetail.jsx OrderPanel

**Previous state**: All 4 buttons used `stageAction` (create pending action for approval queue).

**New state**:
- **Cancel** → `executeAction('cancel')` → `POST /api/v2/tickets/{id}/actions/cancel` → Shopify + email + resolve (direct)
- **Refund** → `executeAction('refund')` → `POST /api/v2/tickets/{id}/actions/refund` → Shopify + email + resolve (direct)
- **Update Address** → `stageAction` → queued in Escalations
- **Reship** → `stageAction` → queued in Escalations

Added `actionLoading` and `actionResult` state to OrderPanel. Success reloads page after 1.8s.

## Outcome

- ✅ Cancel request → AI sends "we'll review it" automatically → Escalations shows pending action for approval
- ✅ Cancel button in conversation → executes in Shopify, sends confirmation, resolves ticket
- ✅ Refund button → same
- ✅ Address/Reship → still staged for approval (not implemented in Shopify yet)
- ✅ Escalations approve → also executes + sends email (via _post_execution_notify from session 17)
