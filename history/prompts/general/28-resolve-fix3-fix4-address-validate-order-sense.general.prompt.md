---
id: 28
title: Complete Fix3 address validation and Fix4 AI order sense
stage: general
date: 2026-06-05
surface: agent
model: claude-sonnet-4-6
feature: none
branch: updated_whole_damn_product
user: Syeda Hafsa
command: continue session
labels: ["address_validation", "order_state", "common_sense", "shopify", "fix3", "fix4"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/services/return_actions_integration.py
  - backend/src/services/shopify_service.py
  - backend/src/services/actions_service.py
  - backend/src/services/tools.py
  - backend/src/agent/customer_success_agent.py
tests:
  - none
---

## Prompt

Continuation of RESOLV prompt session. Complete Fix 3 (remaining items) and Fix 4 (AI Common Sense).

Fix 3 remaining:
- Add `_validate_address` method to `ReturnActionsIntegration`
- Update `shopify_service.update_shipping_address` to include name fields (first_name/last_name)
- Update `actions_service.approve_action` to pass `customer_name` to `update_shipping_address`

Fix 4 (AI Common Sense):
- Add order state (financial_status, cancelled_at) to `tools.py` get_order_status result
- Add state-derived notes to `_build_order_context` (already cancelled, already refunded, already fulfilled)
- Add COMMON SENSE rules to `_construct_v3_prompt` system prompt

## Response snapshot

Added `_validate_address(parsed) -> (is_valid, missing_fields)` to `ReturnActionsIntegration`. Requires address1 + city + country.

Updated `ShopifyClient.update_shipping_address` to accept optional `customer_name` param, splits into first_name/last_name for Shopify payload. Falls back to existing order name fields if no name provided.

Updated `actions_service.approve_action` change_address block to pass `customer_name=action.get("customer_name")`.

Added `financial_status` and `cancelled_at` to `tools.py` `get_order_status` result dict.

Updated `_build_order_context` in `customer_success_agent.py` to emit `COMMON SENSE RULES FOR THIS ORDER` block when order is already cancelled, refunded, or fulfilled.

Added `COMMON SENSE — READ ORDER STATUS BEFORE RESPONDING` block to `_construct_v3_prompt` system prompt instructing the LLM not to offer actions that are already done or impossible given order state.

## Outcome

- ✅ Impact: AI will no longer offer refunds on already-refunded orders, cancellations on already-cancelled/fulfilled orders, or address changes on fulfilled orders. Shopify address update now always includes name fields. Address validation blocks incomplete addresses before queueing.
- 🧪 Tests: manual testing required
- 📁 Files: 5 files modified
- 🔁 Next prompts: test all 4 flows end-to-end with demo data
- 🧠 Reflection: Order state context must come from live Shopify data (tools.py) to be reliable — that's the right source to tap.

## Evaluation notes (flywheel)

- Failure modes observed: LLM was offering refunds/cancels on orders where those actions were impossible
- Graders run and results (PASS/FAIL): none automated
- Prompt variant (if applicable): null
- Next experiment: test with real cancelled + refunded orders from Shopify sandbox
