---
id: 22
title: Extend Action Types Reship Address Cancel
stage: general
date: 2026-06-04
surface: agent
model: claude-sonnet-4-6
feature: none
branch: updated_whole_damn_product
user: Syeda Hafsa
command: multi-file code edit
labels: ["actions","reship","cancel","address-change","intent-detection","ticket-id","bug-fix"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/services/return_actions_integration.py
  - backend/src/services/actions_manager.py
  - backend/src/services/actions_service.py
  - backend/src/agent/customer_success_agent.py
  - backend/src/workers/message_processor.py
tests:
  - AST syntax check on all 5 files (PASS)
  - ALL_ACTION_KEYWORDS present on ReturnActionsIntegration class (PASS)
  - ActionType.RESHIP present in enum (PASS)
  - process_customer_query accepts ticket_id param (PASS)
  - message_processor passes early_ticket_id to generate_channel_appropriate_response (PASS)
---

## Prompt

Make specific code changes across 5 backend files:

CHANGE 1: return_actions_integration.py — Replace RETURN_KEYWORDS list and _ACTION_TYPE_MAP; add CANCEL_KEYWORDS, ADDRESS_KEYWORDS, RESHIP_KEYWORDS, ALL_ACTION_KEYWORDS class attributes; update should_check_return_eligibility to use ALL_ACTION_KEYWORDS; add _detect_intent_type and _extract_address_text helpers; completely replace handle_return_intent with new version that branches on address_change / reship / cancel / refund intent types.

CHANGE 2: actions_manager.py — Fix hardcoded "#1002" log line to use #{order_id}; add staging_required / action_hint / fulfillment_status to the unfulfilled-order return dict; fix _find_exchange_size bug where loop variable `step` (a string) was used as integer offset.

CHANGE 3: actions_service.py — Add RESHIP to ActionType enum; fix change_address approve block to not hard-fail when no address is provided (return manual_action_required instead); add reship handling block; add change_address and reship email templates in _post_execution_notify.

CHANGE 4: customer_success_agent.py — Add ticket_id param to process_customer_query and generate_channel_appropriate_response; pass ticket_id through to handle_return_intent call.

CHANGE 5: message_processor.py — Pass ticket_id=early_ticket_id to generate_channel_appropriate_response call.

## Response snapshot

All 5 files edited precisely per spec. All validations passed:
- return_actions_integration.py: ALL_ACTION_KEYWORDS class attribute present; should_check_return_eligibility uses it; _detect_intent_type and _extract_address_text added before handle_return_intent; handle_return_intent fully replaced with intent-branched version.
- actions_manager.py: log line fixed; unfulfilled return dict extended with staging_required/action_hint/fulfillment_status; _find_exchange_size loop variable renamed from (direction, step) to (offset, label) with new_idx = current_idx + offset.
- actions_service.py: RESHIP added to ActionType; change_address block now returns manual_action_required dict when no address; reship block added; two new email templates added to _post_execution_notify.
- customer_success_agent.py: ticket_id added to both method signatures; passed through to handle_return_intent.
- message_processor.py: ticket_id=early_ticket_id passed to generate_channel_appropriate_response.
All 5 files pass Python AST syntax validation.

## Outcome

- Impact: Agent now handles 4 action intent types (refund, cancel, address_change, reship) with correct routing; ticket_id is threaded end-to-end from message processor through agent to action creation; bug in _find_exchange_size (string used as numeric offset) is fixed; address approval no longer hard-fails when no structured address is extracted.
- Tests: AST syntax check + 4 attribute/signature validation checks all PASS
- Files: 5 backend files modified
- Next prompts: Test end-to-end with a reship email; verify address_change action shows manual_action_required flag in UI
- Reflection: The _find_exchange_size bug (step string used as array index) would have caused a silent TypeError at runtime — worth adding a unit test for that function.

## Evaluation notes (flywheel)

- Failure modes observed: none
- Graders run and results (PASS/FAIL): AST parse PASS, attribute validation PASS
- Prompt variant (if applicable): null
- Next experiment: Add unit tests for _detect_intent_type and _find_exchange_size
