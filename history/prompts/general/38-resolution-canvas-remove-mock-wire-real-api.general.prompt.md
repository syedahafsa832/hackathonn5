---
id: 38
title: Resolution canvas remove mock wire real API
stage: general
date: 2026-06-08
surface: agent
model: claude-sonnet-4-6
feature: chat-widget
branch: updated_whole_damn_product
user: Syeda Hafsa
command: Remove mock from resolution-canvas and wire real backend API
labels: ["chat_widget", "resolution_canvas", "mock_removal", "api_wiring", "order_data", "confidence"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - resolution-canvas/app/chat-demo/page.tsx
  - resolution-canvas/.env.local.example
  - backend/src/api/routes/v2_chat_widget.py
  - backend/src/agent/customer_success_agent.py
tests:
  - none
---

## Prompt

see this another ai agent has built it now as you are experienced remove mock and make it work

## Response snapshot

**Resolution-canvas is a Next.js app** (`resolution-canvas/`) with a fancy chat widget component. Another AI agent built it with a demo mock switch: `if (apiBaseUrl === 'demo') { getMockResponse() } else { real fetch }`.

**Changes made:**

1. **`resolution-canvas/app/chat-demo/page.tsx`**: Replaced hardcoded `brandId="demo-brand-uuid"`, `orgId="demo-org-uuid"`, `apiBaseUrl="demo"` with env vars:
   - `brandId={process.env.NEXT_PUBLIC_BRAND_ID || ''}`
   - `orgId={process.env.NEXT_PUBLIC_ORG_ID || ''}`
   - `apiBaseUrl={process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}`

2. **`resolution-canvas/.env.local.example`**: Created with `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_BRAND_ID`, `NEXT_PUBLIC_ORG_ID`.

3. **`backend/src/api/routes/v2_chat_widget.py`**:
   - Extended `ChatResponse` with `confidence`, `resolution_step`, `order_data`, `customer_name`
   - Added `_map_resolution_step(intent, status)` helper
   - Chat endpoint now returns all four new fields from agent result

4. **`backend/src/agent/customer_success_agent.py`**:
   - Before `return structured`, added logic to populate `structured["order_data"]` from `tool_results["order_status"]` if Shopify order was fetched
   - Maps `fulfillment_status` → `'fulfilled'|'processing'|'cancelled'|'pending'`
   - Maps `financial_status` → `'paid'|'refunded'|'pending'`
   - Includes `orderNumber`, `items`, `status`, `paymentStatus`, `cancelledAt`

## Outcome

- ✅ Resolution-canvas widget switches from mock to real API via env vars (no code change needed)
- ✅ Backend now returns `confidence`, `resolution_step`, `order_data`, `customer_name` in chat response
- ✅ Order card in resolution-canvas widget will populate when Shopify order is fetched
- 📁 Files: 4 files modified, 1 file created
- 🔁 Next: copy `.env.local.example` to `.env.local`, fill in brand/org UUIDs, run both servers

## Evaluation notes (flywheel)

- Failure modes observed: none
- Graders run and results (PASS/FAIL): none
- Prompt variant (if applicable): none
- Next experiment (smallest change to try): test order card rendering with a real order number query
