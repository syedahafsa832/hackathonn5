---
name: Fix wrong "address updated" email on manual-action path
description: Fixed confirmation email for change_address sending "address updated" even when manual_action_required=True (address wasn't actually changed in Shopify).
type: general
stage: general
date: 2026-06-05
surface: agent
model: claude-sonnet-4-6
branch: updated_whole_damn_product
user: Syeda Hafsa
labels: ["bugfix", "email", "change_address", "actions_service"]
---

## Prompt

User reported: email saying "Your shipping address has been updated for order #1006" was sent, but the address was NOT updated in Shopify (still shows "No shipping address provided"). The Shopify order shows a refund, not an address change.

## Root Cause

`_post_execution_notify` in `actions_service.py` (line ~498) had a single email body for `change_address` that always said "Your shipping address has been updated." 

However, `approve_action` for `change_address` has two paths:
1. If `new_address` structured data exists → calls `shopify_client.update_shipping_address()` (actually updates)
2. If no structured address → returns `{"success": True, "manual_action_required": True, ...}` and does NOT update Shopify

The raw address text from the LLM (`intent_result.raw_address`) is stored only in `ai_reasoning`, not in `extracted_data["new_address"]`, so the manual path always triggers. The confirmation email was sent before checking whether the address was really updated.

## Fix

`backend/src/services/actions_service.py` — `_post_execution_notify` method:

Added `execution_result.get("manual_action_required")` check for `change_address`:
- **Manual path**: sends "We've received your address change request and our team is updating it right now."
- **API path** (address actually updated): sends "Your shipping address has been updated."

## Files Changed

- `backend/src/services/actions_service.py` — `_post_execution_notify` method, change_address email body
