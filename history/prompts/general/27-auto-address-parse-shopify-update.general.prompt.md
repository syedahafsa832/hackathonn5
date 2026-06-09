---
name: Auto-parse address and update Shopify automatically on approve
description: Added LLM address parsing so address changes update Shopify automatically instead of requiring manual admin action.
type: general
stage: general
date: 2026-06-05
surface: agent
model: claude-sonnet-4-6
branch: updated_whole_damn_product
user: Syeda Hafsa
labels: ["address_change", "shopify", "llm", "auto-update"]
---

## Prompt

User asked: "does it update address automatically or manually?" then "yeahhhhhhhhhhhh do it"

## What Was Done

Added `parse_address()` method to `IntentDetector` that makes a second Mistral call to parse raw address text into structured Shopify fields (address1, address2, city, province, zip, country).

Flow:
1. Intent detection (existing): extracts `raw_address = "123 Main St, New York, NY 10001"`
2. Address parsing (new): Mistral call → `{address1: "123 Main St", city: "New York", province: "NY", zip: "10001", country: "US"}`
3. Stored as `extracted_data["new_address"]` in the actions DB record
4. On approve: `actions_service.approve_action` finds `new_address` → calls `shopify_client.update_shipping_address()` automatically
5. UI shows: `✓ Shipping address updated automatically in Shopify.`

Fail-open: if Mistral unavailable or address can't be parsed (incomplete fields), falls back to manual queue path.

## Files Changed

- `backend/src/services/intent_detector.py` — `ADDRESS_PARSE_PROMPT` + `IntentDetector.parse_address()` method
- `backend/src/services/return_actions_integration.py` — calls `parse_address()`, passes `structured_address` to `_create_action`, updated `action_context` message
- `_create_action` stores `structured_address` as `extracted_data["new_address"]` (the key `approve_action` already checks)
- `ai-ops-console/src/pages/Actions.jsx` — updated success message to say "updated automatically"

## Security Note

User shared Mistral API keys in chat — advised to rotate immediately at console.mistral.ai. Keys are read from `MISTRAL_API_KEY` env var only, never hardcoded.
