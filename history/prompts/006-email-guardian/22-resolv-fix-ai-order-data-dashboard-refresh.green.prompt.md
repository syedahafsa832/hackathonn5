---
id: 22
title: Fix AI Order Data Injection and Dashboard Refresh Button
stage: green
date: 2026-05-24
surface: agent
model: claude-sonnet-4-6
feature: 006-email-guardian
branch: 006-email-guardian
user: Syeda Hafsa
command: continuation
labels: ["bugfix", "ai-agent", "shopify", "dashboard", "order-data"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/services/tools.py
  - backend/src/agent/customer_success_agent.py
  - ai-ops-console/src/pages/Dashboard.jsx
  - ai-ops-console/src/index.css
tests:
  - manual: send order inquiry email and verify AI uses correct product name
  - manual: click dashboard refresh button and verify spinner + timestamp update
---

## Prompt

Fix 1: Shopify order data not reaching AI prompt — AI replies with wrong product ("cashmere wrap in blush pink") instead of actual ordered item. Root cause: system prompt hardcoded "Aurelio & Finch" causing LLM to hallucinate that brand's products, and tool_context was a weak one-liner not strong enough to override.

Fix 2: Add manual refresh button to Dashboard with last-refreshed timestamp.

Fix 3: Verify cancel/refund Shopify API execution — already fully implemented in actions_service.py (no changes needed).

## Response snapshot

Three fixes implemented:

**Fix 1 (customer_success_agent.py + tools.py)**:
- Brand lookup now fetches `name` field without requiring `shopify_connected=true` filter, stores as `_brand_name`
- `tool_context` rebuilt to include explicit structured order data: order number, status, total, each line item with variant/price/qty on its own line
- Added CRITICAL instruction block in system prompt: "⚠ LIVE DATA FROM SHOPIFY — USE ONLY THESE DETAILS"
- `_construct_v3_prompt` now accepts `brand_name` param; uses it in role definition and sign-off
- `tools.py` `get_order_status` Shopify path now returns `price`, `variant_title`, `sku` per line item
- Fallback sign-off no longer hardcodes "Aurelio & Finch"

**Fix 2 (Dashboard.jsx + index.css)**:
- Destructures `refetch` from `useStats()` and `useConversations()`
- Added `handleRefresh` callback that awaits both refetches and updates `lastRefreshed` state
- Added Dashboard header bar with "Updated HH:MM:SS" timestamp and Refresh button with spinning ↻ icon during load
- Added `@keyframes spin` to index.css

**Fix 3**: Already implemented — `actions_service.approve_action()` calls `shopify_client.process_refund()` / `shopify_client.cancel_order()` via `ShopifyClient._request()`. No changes needed.

## Outcome

- ✅ Impact: AI will now reference actual product names from Shopify instead of hallucinating brand-specific products. Dashboard has manual refresh with visual feedback.
- 🧪 Tests: Send test order inquiry email; verify AI reply cites correct item from Shopify order
- 📁 Files: 4 files changed
- 🔁 Next prompts: Test with real email containing order number; verify brand name shows in AI sign-off
- 🧠 Reflection: Root cause was dual: (1) brand name in system prompt activated LLM training-data products for "Aurelio & Finch"; (2) tool_context was too weak. Both required fixing together.

## Evaluation notes (flywheel)

- Failure modes observed: LLM ignores weak tool_context when system prompt anchors it to a specific brand with known product catalog
- Graders run and results (PASS/FAIL): manual only
- Prompt variant (if applicable): added ⚠ CRITICAL block above ORDER INFO section
- Next experiment: if AI still hallucinates, move order data into the user message turn instead of system prompt
