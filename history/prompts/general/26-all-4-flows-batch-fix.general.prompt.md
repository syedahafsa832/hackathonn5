---
name: Batch fix — all 4 action flows (cancel, address, reship, refund)
description: Fixed 5 bugs across cancel/address/reship/refund flows to make all 4 action types work end-to-end.
type: general
stage: general
date: 2026-06-05
surface: agent
model: claude-sonnet-4-6
branch: updated_whole_damn_product
user: Syeda Hafsa
labels: ["bugfix", "actions", "cancel", "address_change", "reship", "refund", "ui"]
---

## Prompt

"noww do alll the fixes at onceee like these all should work" — 4 test flows:
cancel (unfulfilled), address change, reship/lost, refund (fulfilled).
For each: action card must appear with right label/button, approve must work, correct UI message shown.

## Bugs Fixed

### Bug 1: Wrong address-change email (FIXED IN PRIOR SESSION)
`_post_execution_notify` sent "Your shipping address has been updated" even when `manual_action_required=True`.
Now sends "We've received your request and team is updating it" for manual path.

### Bug 2: Double `#` in cancel_order UI message
`EXECUTION_MESSAGES.cancel_order` template: `"Order #${r?.order_name}"` + Shopify returns `order_name="#1006"` → "Order ##1006".
**Fix:** Removed the extra `#` from the template in `Actions.jsx`.

### Bug 3: Address change — raw address not in `extracted_data`
LLM extracts `raw_address` but it was only stored in `ai_reasoning`. Admin clicking approve saw generic "update manually" with no address details.
**Fix:** Added `new_address_text` param to `_create_action`, store it in `extracted_data`, pass it through to `execution_result`, display it in the approval card message.

### Bug 4: `brand_id` not passed to `create_action` or stored in DB
`return_actions_integration._create_action` didn't pass `brand_id` to `actions_service.create_action`, and `create_action` didn't write it to the DB `action_data`. Notification fallback queried by `tenant_id` (worked for single-brand tenants) but broke for multi-brand.
**Fix:** Pass `brand_id=brand_id` in the call, store `"brand_id": brand_id` in `action_data` when present.

### Bug 5: `new_address_text` not in `execution_result` in `v2_actions.py`
Same manual address change path in `v2_actions.py` approve route also missing `new_address_text`.
**Fix:** Same pattern applied to `v2_actions.py`.

## Files Changed

- `ai-ops-console/src/pages/Actions.jsx` — EXECUTION_MESSAGES cancel/address templates
- `backend/src/services/return_actions_integration.py` — `_create_action` signature + extracted_data + brand_id
- `backend/src/services/actions_service.py` — `create_action` stores brand_id; `approve_action` adds new_address_text to execution_result
- `backend/src/api/routes/v2_actions.py` — same new_address_text fix in approve route

## Expected test results after container restart

| Flow | Card label | Button | Approve result |
|------|-----------|--------|---------------|
| Cancel unfulfilled | Cancel Order | Cancel Order | "✓ Order #1006 cancelled. Stock restocked." |
| Address change | Address Change | Update Address | "✓ Queued — update manually. New address: 123 Main St..." |
| Reship | Reship Order | Arrange Reship | "✓ Queued — create replacement shipment in Shopify admin." |
| Refund fulfilled | Refund | Issue Refund | "✓ $xxx.xx refunded via Shopify." |
