---
name: Security audit — per-tenant Shopify credentials
description: Fixed ActionsManager using global env vars for Shopify calls; now routes through per-brand ShopifyClient. Added reship to all validators and UI.
type: general
stage: general
date: 2026-06-04
surface: agent
model: claude-sonnet-4-6
branch: updated_whole_damn_product
user: Syeda Hafsa
labels: ["security", "shopify", "multi-tenant", "actions", "reship"]
---

## Prompt

(Continuation from prior context) Security audit requested before connecting real user Shopify accounts. Fix ActionsManager global env var credentials, add reship action type throughout, and make address change/reship buttons show correctly in the UI.

## Response

### Files Changed

- `backend/src/services/actions_manager.py`
  - Renamed `self.shop_name`/`self.shopify_token` → `self._legacy_shop_name`/`self._legacy_token` with clear legacy label
  - Added `tenant_id`/`brand_id` params to `check_return_eligibility`
  - Rewrote `_get_order_from_shopify`: when `tenant_id` provided, uses `shopify_service.get_client_for_tenant(tenant_id)` (per-brand encrypted creds). Legacy env-var path only runs when no tenant context.
  - Fixed hardcoded `#1002` in "not found" error → `f"#{order_id}"`
  - Added credential guard to `_execute_refund` / `_execute_exchange` (legacy paths)

- `backend/src/services/return_actions_integration.py`
  - `check_return_eligibility` call now passes `tenant_id=tenant_id, brand_id=brand_id`

- `backend/src/api/routes/saas_actions.py`
  - Added `reship` to `CreateActionRequest.action_type` validator pattern

- `backend/src/api/routes/v2_actions.py`
  - Added `reship` to `CreateActionRequest.action_type` validator pattern
  - Added `change_address` (no structured address) and `reship` manual-flag branches in approve route

- `ai-ops-console/src/pages/Actions.jsx`
  - Added lowercase `reship` to `ACTION_LABELS`, `ACTION_EXECUTE_LABELS`, `EXECUTION_MESSAGES`
  - Fixed `change_address` execution message to show manual flag text when no Shopify address update occurred

### Security Impact

Before: every eligibility check hit the single global Shopify store from env vars — wrong store for every real user.
After: `check_return_eligibility` fetches per-brand creds via `shopify_service.get_client_for_tenant`, the same path used by `actions_service.approve_action`. Legacy env-var path is clearly labelled and only used in dev with no tenant context.
