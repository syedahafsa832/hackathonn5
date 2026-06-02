---
id: 18
title: Multi-Tenant Isolation + Shopify Order Lookup Wiring
stage: green
date: 2026-05-19
surface: agent
model: claude-sonnet-4-6
feature: 006-email-guardian
branch: 006-email-guardian
user: aicoders123@gmail.com
command: RESOLV — FIX MULTI-TENANT + WIRE SHOPIFY ORDER LOOKUP
labels: ["multi-tenant","shopify","oauth","security","fix"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/api/routes/brand_gmail.py
  - backend/src/services/tools.py
  - backend/src/agent/customer_success_agent.py
  - backend/src/workers/message_processor.py
tests:
  - manual: send email with order number to connected Gmail and verify AI uses brand Shopify credentials
  - manual: verify brand with tenant_id=NULL cannot be accessed via another tenant's auth token
---

## Prompt

RESOLV — FIX MULTI-TENANT + WIRE SHOPIFY ORDER LOOKUP

Three fixes requested:
1. Gmail polling is not tenant-isolated (worker polls all brands, creates tickets under wrong org)
2. Gmail OAuth callback may store tokens without organization_id
3. Shopify is connected in UI but backend never uses it — AI has no real order data

## Response snapshot

**Fix 1 (Gmail polling isolation)**: Audit confirmed the polling loop IS already tenant-isolated — each brand has its own Gmail tokens, tickets are tagged with `store_id=brand_id`. However, `_get_owned_brand()` in `brand_gmail.py` had a NULL tenant_id security gap: if `brand.tenant_id` was NULL, any authenticated tenant could access that brand. Fixed by changing `if brand_tenant and ...` to `if not brand_tenant or ...`.

**Fix 2 (OAuth callback)**: Already secure via HMAC-signed state token (brand_id + 10min expiry). No oauth_states table needed. The same NULL tenant_id gap fixed above covers the auth surface.

**Fix 3 (Shopify order lookup)**: Real work. The agent called `v3_tools.get_order_status()` which used global env vars (`SHOPIFY_SHOP_NAME`, `SHOPIFY_ACCESS_TOKEN`) instead of per-brand credentials stored in `brands.shopify_domain` / `brands.shopify_access_token`. Changes:

- `tools.py` `get_order_status()` now accepts `shop_domain` and `access_token` kwargs; normalizes domain to handle both `shop` and `shop.myshopify.com` formats; uses `?name=%23{order_id}&status=any&limit=1`
- `customer_success_agent.py` `process_customer_query()` and `generate_channel_appropriate_response()` now accept `store_id`; looks up brand's Shopify creds (with `decrypt_token`) before tool calls; passes them to `get_order_status()`
- `message_processor.py` passes `store_id` to `generate_channel_appropriate_response()`

## Outcome

- ✅ Impact: Brands with their own Shopify connected will now get real order data in AI replies
- 🧪 Tests: Manual — send test email mentioning an order number, verify AI reply references correct order details
- 📁 Files: 4 files modified
- 🔁 Next prompts: Add detected_order_id to ticket detail UI; verify order lookup works end-to-end with real Shopify store
- 🧠 Reflection: The polling isolation was already correct; the real bug was Shopify credentials not flowing from brand → agent → tools

## Evaluation notes (flywheel)

- Failure modes observed: Global env var Shopify creds used instead of per-brand; NULL tenant_id allowed cross-tenant brand access
- Graders run and results (PASS/FAIL): manual test pending
- Prompt variant (if applicable): null
- Next experiment: Add `detected_order_id` display in ticket detail panel
