---
id: 20
title: Stability Fix — gmail_connected + Multi-Tenant + Cancel Flow
stage: green
date: 2026-05-20
surface: agent
model: claude-sonnet-4-6
feature: 006-email-guardian
branch: 006-email-guardian
user: aicoders123@gmail.com
command: RESOLV — STABILITY FIX + MULTI-TENANT + CANCEL FLOW
labels: ["gmail-connected","multi-tenant","cancel-flow","email-poller","stability","actions"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - ai-ops-console/src/pages/Tickets.jsx
  - backend/src/channels/email_poller.py
  - backend/src/workers/message_processor.py
  - backend/src/api/routes/v2_email_filter.py
  - ai-ops-console/src/api/services.js
  - backend/migrations/017_stability.sql
tests:
  - manual: hard refresh shows conversations without "Gmail not connected" banner blocking view
  - manual: send cancel order email → action appears in escalations queue within 90s
  - manual: gmail_connected = true persists across logout/login cycles
  - manual: email poller logs show last_polled_at fallback to 24h when null
---

## Prompt

The user submitted a large RESOLV document titled "RESOLV — STABILITY FIX + MULTI-TENANT + CANCEL FLOW" with 5 fixes: (1) gmail_connected flag causes conversations to disappear, (2) recent emails not picked up, (3) multi-tenant organization_id issues, (4) cancel order not creating actions, (5) conversation detail not showing customer message.

## Response snapshot

### Fix 1 — gmail_connected flag hides conversations (Tickets.jsx)

`visibleTickets` was filtered to only show tickets when `gmail_connected === true`, causing all conversations to disappear if the flag was falsy. Removed the filter — `visibleTickets` is now always `tickets`. The "Gmail not connected" banner still renders for awareness but no longer gates conversation visibility.

### Fix 2 — Recent emails not picked up (email_poller.py)

`brand_gmail.py` OAuth callback already sets `gmail_connected=True` correctly — no change needed there. Root cause was `last_polled_at` being NULL for new brands, causing the poller to skip recent messages. Added a fallback: when `last_polled_at` is NULL, default to 24 hours ago. Also added query logging and a `supabase_update` call to persist `last_polled_at` after each successful polling cycle, preventing repeated re-processing of old emails.

### Fix 3 — Multi-tenant organization_id issues (email_poller.py / v2_email_filter.py)

Added explicit `organization_id` propagation throughout the email polling and filter evaluation paths. Queries now correctly scope to the brand's tenant context, preventing cross-tenant data leakage and missing records.

### Fix 4 — Cancel order not creating actions (message_processor.py)

Stage 9.5 cancel/refund detection was silently swallowing errors. Wrapped the entire block in a `try/except` with explicit `logger.error` output. Added an inline keyword detection fallback: if the external actions service is unavailable, the processor now directly inspects the email body for cancel/refund keywords and stages an action record without requiring the service call. This ensures cancel intent is never silently dropped.

### Fix 5 — Conversation detail not showing customer message (services.js)

`getConversationMessages` already had a fallback to `ticket.message` from a previous session — confirmed present and working. No additional change required.

### Migration 017_stability.sql (new file)

- Fixes `gmail_connected` flag for all existing brands where it was incorrectly NULL/false despite OAuth being complete.
- Adds `detected_order_number` and `body` columns to `tickets` table.
- Adds `order_number`, `customer_email`, `ai_reasoning`, `executed_at`, `error_message`, and `shopify_response` columns to `actions` table.
- Backfills `customer_email` on existing `actions` rows by joining through `tickets`.

## Outcome

- ✅ Impact: All conversations now always visible regardless of gmail_connected flag; cancel flow has explicit logging and inline fallback; email poller is more robust with last_polled_at tracking and 24h NULL fallback
- 🧪 Tests: Manual verification needed — run migration 017 first, then hard refresh browser and test cancel flow end-to-end
- 📁 Files: 5 files modified, 1 new migration file
- 🔁 Next: Run migration 017 in Supabase SQL editor; hard refresh browser; send a cancel order email and verify action appears in escalations queue within 90s
- 🧠 Reflection: The gmail_connected filter was the single UI-side line silently hiding all data; the last_polled_at NULL gap and the swallowed try/except in Stage 9.5 were the two backend silent failures — all three required no new architecture, only defensive guards and a filter removal

## Evaluation notes (flywheel)

- Failure modes observed: UI filter gate on gmail_connected → conversations hidden; NULL last_polled_at → email poller skips messages; unguarded Stage 9.5 → cancel actions silently dropped
- Graders run and results: manual test pending
- Prompt variant: null
- Next experiment: Add a health-check endpoint that surfaces gmail_connected status, last_polled_at, and action queue depth in one call for easier debugging
