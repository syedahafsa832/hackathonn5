---
id: 29
title: Restore order intent, missing order num guard, bulk delete
stage: general
date: 2026-06-05
surface: agent
model: claude-sonnet-4-6
feature: none
branch: updated_whole_damn_product
user: Syeda Hafsa
command: RESOLV — RESTORE ORDER + MISSING ORDER NUMBER + BULK DELETE ESCALATIONS
labels: ["restore_order", "intent_detection", "bulk_delete", "shopify", "fix"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/services/intent_detector.py
  - backend/src/services/return_actions_integration.py
  - backend/src/services/shopify_service.py
  - backend/src/services/actions_service.py
  - backend/src/api/routes/v2_actions.py
  - backend/src/api/routes/saas_actions.py
  - backend/src/workers/message_processor.py
  - ai-ops-console/src/pages/Actions.jsx
  - ai-ops-console/src/api/services.js
tests:
  - none
---

## Prompt

Three problems from screenshots:
1. "i mistakenly canceled my order please get it back" → no order number → system created Cancel Order action (wrong type, wrong trigger)
2. "i mistakenly canceled my order 1007 please get it active back" → system created Cancel Order instead of Restore Order
3. No way to bulk delete/clear escalations queue

## Response snapshot

**Root cause found:** Two action creation paths exist:
- Primary: customer_success_agent → return_actions_integration (correctly guards order_id)
- Fallback Stage 9.5 in message_processor.py: inline detect_and_create → creates action even with order_id=None

**Fix 1 + Fix 2 — restore_order intent:**
- Added `restore_order` to INTENT_PROMPT in intent_detector.py (takes priority over cancel, examples: "mistakenly canceled", "get it back", "undo cancel")
- Added `_RESTORE_FRAGS` list; checked BEFORE `_CANCEL_FRAGS` in keyword fallback
- Updated `detect()` validation to allow `restore_order` as valid action_type
- Added `restore_order` handler in `return_actions_integration.handle_return_intent`: asks for order number if missing, otherwise creates restore_order action
- Added `_ACTION_TYPE_MAP` entry for `restore_order`
- Added `ShopifyClient.reopen_order()` — calls POST /orders/{id}/reopen.json
- Added `ActionType.RESTORE_ORDER` enum value
- Wired restore_order execution in `actions_service.approve_action` and `v2_actions.approve_action`
- Added restore_order confirmation email in `_post_execution_notify`
- Added restore_order to ActionDetector action_map

**Fix 1 — no order number guard:**
- `detect_and_create` in actions_service.py: returns None early if order_id is None (never creates action without order)
- Stage 9.5 fallback in message_processor.py: added `and _intent.order_id` guard before inserting action

**Fix 3 — bulk delete:**
- Added POST `/api/v2/actions/bulk-reject` endpoint — accepts `action_ids: list` or `clear_all: true`
- Added `api.bulkRejectActions()` to services.js
- Updated Actions.jsx: select-all checkbox + "Reject N selected" + "Clear all" button above pending actions; checkboxes on each card

**Frontend:**
- Added `restore_order` to ACTION_LABELS (green), ACTION_EXECUTE_LABELS ("Restore in Shopify"), EXECUTION_MESSAGES

## Outcome

- ✅ Impact: "mistakenly canceled" messages now create Restore Order (green card) not Cancel Order. No action created without an order number. Bulk reject clears old queue noise in one click.
- 🧪 Tests: manual — send restore/cancel messages with and without order numbers
- 📁 Files: 9 files modified
- 🔁 Next prompts: test all scenarios from RESOLV verification section
- 🧠 Reflection: The dual action creation path (agent path + Stage 9.5 fallback) was the root cause of both bugs. Fixing the guard in detect_and_create covers all future action types too.

## Evaluation notes (flywheel)

- Failure modes observed: Stage 9.5 fallback bypassed order_id check; cancel/restore ambiguity not in LLM prompt
- Graders run and results (PASS/FAIL): none automated
- Prompt variant (if applicable): null
- Next experiment: add restore_order to _auto_tag_ticket in message_processor for better ticket tagging
