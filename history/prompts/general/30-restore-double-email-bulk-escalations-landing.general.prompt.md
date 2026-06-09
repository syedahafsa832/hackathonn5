---
id: 30
title: Fix restore double email, bulk escalation close, landing page
stage: general
date: 2026-06-06
surface: agent
model: claude-sonnet-4-6
feature: none
branch: updated_whole_damn_product
user: Syeda Hafsa
command: RESOLV — THREE FIXES (restore double email, bulk escalation delete, landing page)
labels: ["restore_order", "double_email", "bulk_delete", "escalations", "shopify_reopen", "fix"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/workers/message_processor.py
  - backend/src/services/actions_service.py
  - backend/src/services/return_actions_integration.py
  - backend/src/api/routes/v2_actions.py
  - backend/src/api/routes/v2_tickets.py
  - ai-ops-console/src/api/services.js
  - ai-ops-console/src/pages/Actions.jsx
tests:
  - none
---

## Prompt

Three problems:

1. RESTORE ORDER — TWO CONTRADICTORY EMAILS: Customer says "restore my order 1007" → AI sends correct email "can't be restored, sorry". But system ALSO creates a restore_order action. When admin clicks Approve → second email sent saying "Our team is reactivating it right now..." — contradicts the first email. Root cause: Stage 9.5 fallback in message_processor.py creates restore_order action even when return_actions_integration correctly doesn't. Fix: check restocked status BEFORE creating action; if restocked → no action; if NOT restocked → create action for human to approve + try Shopify reopen.json on approval.

2. BULK DELETE ESCALATIONS — 11 escalated tickets with no way to clear them. Need select-all + per-row checkboxes + "Mark N resolved" + "Clear all" button on Escalated Conversations table.

3. LANDING PAGE — "One console for all your brands" claim is false (each brand needs its own login). Remove or replace with honest copy.

## Response snapshot

**Fix 1 — restore_order double email:**
- `message_processor.py`: Removed `restore_order` from Stage 9.5 fallback `_atype_map` — prevents spurious action creation from that path
- `actions_service.py` `detect_and_create`: Skip restore_order (return None) — handled exclusively by primary agent path
- `return_actions_integration.py`: Added restocked-status check using `existing_tool_results["order_status"]`:
  - If restocked → no action, tell customer to reorder
  - If cancelled + not restocked → create restore_order action (Shopify reopen may work)
  - If no order data → default safe message (no action)
- `actions_service.py` `approve_action` for restore_order: Now calls `shopify_client.get_order()` → checks restocked → raises ShopifyError if restocked → calls `reopen_order()` if OK
- `v2_actions.py` `approve_action` for restore_order: Same restocked-check + reopen logic
- `_post_execution_notify` for restore_order: Changed email from "Our team is reactivating it" → "Great news! Your order has been restored and is now active again."
- `Actions.jsx` EXECUTION_MESSAGES: restore_order now says "Order X has been restored and is active again."

**Fix 2 — bulk escalation close:**
- `v2_tickets.py`: Added `BulkEscalationCloseRequest` model + `POST /api/v2/tickets/bulk-escalation-close` endpoint (accepts `ticket_ids: list` or `close_all: true`)
- `services.js`: Added `bulkCloseEscalations()`
- `Actions.jsx`: Added `selectedEscalationIds` + `bulkEscalWorking` state, `toggleEscalation`/`toggleAllEscalations`/`handleBulkCloseEscalations`/`handleCloseAllEscalations` handlers, toolbar with Select-all + "Mark N resolved" + "Clear all" buttons above Escalated Conversations table, per-row checkboxes, `refetch: refetchEscalations` from `useEscalations`

**Fix 3 — landing page:**
- NOT FOUND in codebase. The string "One console for all your brands" does not exist in any file in this repo. The landing page must be a separate external site. User needs to share the file or URL.

## Outcome

- ✅ Impact: restore_order now only creates an action when the order is cancelled and NOT restocked. No contradictory emails. Bulk escalation close clears queue in one click. Landing page fix pending (not in repo).
- 🧪 Tests: manual — send restore message with restocked order (should get "can't restore"), send with non-restocked cancelled order (should queue action, approve should try reopen.json)
- 📁 Files: 7 files modified
- 🔁 Next prompts: test all 3 scenarios; locate landing page file for Fix 3
- 🧠 Reflection: The dual action creation path (Stage 9.5 + primary agent) was again the root cause of the double email. Blocking restore_order from detect_and_create and Stage 9.5 ensures it can only be created with the correct restocked-status check.

## Evaluation notes (flywheel)

- Failure modes observed: Stage 9.5 bypass created restore_order actions with no restocked check; _post_execution_notify sent misleading email on approval of spurious action
- Graders run and results (PASS/FAIL): none automated
- Prompt variant (if applicable): null
- Next experiment: add restore_order to _auto_tag_ticket; test Shopify reopen.json on a real non-restocked cancelled order
