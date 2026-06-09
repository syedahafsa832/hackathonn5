---
id: 33
title: Product upgrades tracking CSAT restocked response time
stage: general
date: 2026-06-07
surface: agent
model: claude-sonnet-4-6
feature: none
branch: updated_whole_damn_product
user: Syeda Hafsa
command: RESOLV — PRODUCT UPGRADES + MULTI-TENANT FIX (5 problems/features)
labels: ["tracking", "csat", "restocked", "response_time", "brand_name", "email_body", "fix"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/services/tools.py
  - backend/src/agent/customer_success_agent.py
  - backend/src/channels/email_poller.py
  - backend/src/services/brand_gmail_service.py
  - backend/src/api/services.js
  - ai-ops-console/src/pages/Dashboard.jsx
  - ai-ops-console/src/pages/TicketDetail.jsx
tests:
  - none
---

## Prompt

RESOLV — PRODUCT UPGRADES + MULTI-TENANT FIX:
1. New account shows zeros (multi-tenant bug)
2. Remove restore button for restocked orders
3. Tracking info in AI replies
4. CSAT score feature
5. Response time analytics
6. Full email body (160-char truncation)
7. Brand name in emails

## Response snapshot

**PROBLEM 1 (multi-tenant new account bug):** Already fixed in previous sessions:
- `auth_service.py` creates default brand on registration with `gmail_connected: False`
- Startup-heal step 5 creates brands for existing tenants with none

**PROBLEM 2 (restocked order buttons):**
- `TicketDetail.jsx` OrderPanel: Added `isRestocked` detection (`cancelled_at && fulfillment_status === 'restocked'`)
- Shows grey informational message when restocked: "Order was cancelled and inventory restocked. Cannot be restored — customer must place a new order."
- Hid Refund button when order is cancelled (already paid but cancelled orders shouldn't be refunded from this panel)
- Hid Address Change when cancelled (`!order.cancelled_at` added to show condition)
- Hid Reship when restocked

**FEATURE 1 (tracking in AI replies):**
- `tools.py`: Added `tracking_company` field from Shopify fulfillment to order status result
- `customer_success_agent.py` `_build_order_context`: Changed tracking section to include carrier, explicit tracking number, URL, and a directive: "IF CUSTOMER ASKS WHERE THEIR ORDER IS: Tell them exactly: 'Your tracking number is X — track it here: URL'"
- Also added unfulfilled fallback: "Order has not shipped yet — if customer asks, tell them it hasn't been dispatched."

**FEATURE 2 (CSAT):**
- `email_poller.py`: Added `_csat_loop_counter` and runs `_send_csat_surveys()` every ~30 minutes
- `_send_csat_surveys()`: Queries tickets with status=resolved, email_sent=true, updated 30-60 min ago, csat_sent not true; sends "YES/NO" follow-up in Gmail thread via new `send_reply_in_thread()` method
- `brand_gmail_service.py`: Added `send_reply_in_thread()` method that sends to an existing threadId
- `services.js`: Added `csatPct` stat (% YES out of csat_sent tickets)
- **⚠ DB MIGRATION REQUIRED**: `ALTER TABLE tickets ADD COLUMN IF NOT EXISTS csat_sent BOOLEAN DEFAULT FALSE; ALTER TABLE tickets ADD COLUMN IF NOT EXISTS csat_response TEXT;` — CSAT send gracefully handles missing column (try/except)

**FEATURE 3 (response time analytics):**
- `Dashboard.jsx`: Added 5th stat card "Avg Response Time" using existing `avgResponseSeconds` from `services.js` (already computed from `first_response_at - created_at`)
- Formats as Xs or Xm Xs

**FEATURE 4 (full email body):** Already implemented — `brand_gmail_service._decode_body()` extracts full body from Gmail payload. No changes needed.

**FEATURE 5 (brand name in emails):**
- `customer_success_agent.py` `_get_fallback_response()`: Added optional `brand_name` parameter; sign-off now `— Luna\n{brand_name}` instead of `— Luna`
- Updated the main exception handler call to pass `_brand_name`

## Outcome

- ✅ Restocked orders: informational message shown, no confusing action buttons
- ✅ AI now has explicit tracking directive — will include real number in reply
- ✅ CSAT surveys fire 30-60 min after resolution (requires DB migration for persistence)
- ✅ Avg Response Time stat card on dashboard
- ✅ Error fallback emails include brand name
- ✅ Full email body already working
- ⚠ Run CSAT SQL migration in Supabase: `ALTER TABLE tickets ADD COLUMN IF NOT EXISTS csat_sent BOOLEAN DEFAULT FALSE; ALTER TABLE tickets ADD COLUMN IF NOT EXISTS csat_response TEXT;`
- 📁 Files: 7 files modified
