---
id: 43
title: Widget Escalation Not Persisting Fix
stage: general
date: 2026-06-09
surface: agent
model: claude-sonnet-4-6
feature: chat-widget
branch: updated_whole_damn_product
user: Syeda Hafsa
command: Widget escalation not flagging for team
labels: ["chat-widget","escalation","bug-fix","tickets","v2_chat_widget"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/api/routes/v2_chat_widget.py
tests:
  - manual: Send angry message in chat widget → check Escalations page shows ticket as escalated
  - manual: Agent exception fallback → ticket should appear in Escalations (not stay "open")
---

## Prompt

"but it didnt flag it for team?" — User showed a widget conversation where Luna said "I've flagged this for my team to take a look" but the ticket never appeared in the Escalations page. Customer had asked "where is my order", order was cancelled, customer reacted with "what the hell who would have done that", triggering Luna's fallback response.

## Response snapshot

Root cause: `v2_chat_widget.py` `supabase_update` at the end of the `/widget/chat` handler only ever wrote `messages`, `customer_email`, `email_sent`, `updated_at` — it never wrote `status` or `escalate`. So even when the agent returned `escalate: True, status: "escalated"`, those fields were silently discarded. The ticket stayed `"open"` in the database forever. The Escalations page queries for `status = "escalated"` so it never surfaced.

Secondary fix: `_get_brand()` was using `is_active: "is.true"` which silently excludes brands with NULL `is_active` (same PostgREST NULL matching bug fixed in other routes).

Fixes applied to `v2_chat_widget.py`:
- Capture `ticket_escalate`, `ticket_status_update`, `ticket_escalation_reason` from agent result inside the try block
- On exception (fallback path), set `ticket_status_update = "escalated"`, `ticket_escalate = True`, `ticket_escalation_reason = f"Agent error: {e}"`
- Build `ticket_update` dict conditionally: write `status`, `escalate`, `escalation_reason` only when non-None/non-False
- Changed `_get_brand` filter from `is_active: "is.true"` → `{"id": f"eq.{brand_id}"}` (no is_active filter)
- Added log line when ticket is escalated for observability

## Outcome

- ✅ Impact: Widget chat escalations now persist to DB and appear in Escalations page; agent exception fallbacks also correctly mark tickets as escalated
- 🧪 Tests: Manual — send upset message in widget, verify ticket appears in Escalations page with status=escalated
- 📁 Files: 1 file modified (v2_chat_widget.py)
- 🔁 Next: Verify Aftership integration with rebuilt container; run Supabase migration for aftership_api_key column
- 🧠 Reflection: The widget route was written as a "simple" endpoint and never wired up the same escalation persistence logic that message_processor.py has — a case where two code paths diverged silently

## Evaluation notes (flywheel)

- Failure modes observed: Agent returns escalate=True but ticket stays open (widget-specific gap vs email processor)
- Graders run and results (PASS/FAIL): Not run
- Prompt variant (if applicable): null
- Next experiment: Add integration test that asserts widget ticket status after escalation trigger
