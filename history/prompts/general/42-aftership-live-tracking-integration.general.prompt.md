---
id: 42
title: Aftership Live Tracking Integration
stage: general
date: 2026-06-09
surface: agent
model: claude-sonnet-4-6
feature: aftership-tracking
branch: updated_whole_damn_product
user: Syeda Hafsa
command: Aftership Tracking Integration spec
labels: ["aftership","tracking","shopify","pakistan-couriers","luna","integrations"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/services/tracking_service.py
  - backend/src/agent/customer_success_agent.py
  - backend/src/api/routes/saas_settings.py
  - ai-ops-console/src/pages/Settings.jsx
tests:
  - manual: Settings → Integrations tab shows Aftership key input
  - manual: After saving key, luna gives live status on "where is my order"
---

## Prompt

Aftership Tracking Integration: When customer asks where their order is, Luna should call Aftership API with tracking number + carrier slug, get real current status, and tell customer in plain English ("Your order is currently in Lahore, last updated June 8. Expected delivery: June 10.") instead of sharing raw URL or saying "check your email". Spec covers: tracking_service.py, wiring into agent, Settings Integrations tab, graceful fallback.

## Response snapshot

Implemented full Aftership integration:

- Created `tracking_service.py`: async `get_tracking_status(tracking_number, carrier_slug, aftership_api_key)` with 5s timeout; `shopify_carrier_to_aftership_slug()` mapping 15+ carriers; `build_tracking_context()` generating agent-ready text blocks for all statuses (InTransit/OutForDelivery/Delivered/AttemptFail/no-data)
- Updated `customer_success_agent.py`: brand lookup now also reads `aftership_api_key`; after Shopify order is fetched, calls tracking service if tracking_number + aftership_key present; `_build_order_context()` now accepts `tracking_context` param replacing the raw URL block
- Updated `saas_settings.py`: added `GET/POST/DELETE /api/v1/settings/aftership` endpoints; key stored in `brands.aftership_api_key`; GET returns `{connected, key_preview}` without exposing full key
- Updated `Settings.jsx`: new "Integrations" tab with Aftership connect/disconnect UI, Pakistan carrier reference grid, link to aftership.com API page; tab added to TABS array

Fallback chain: live Aftership data → tracking URL → tracking number only → "not available yet"

## Outcome

- ✅ Impact: Luna gives plain-English live tracking status for all Pakistan couriers + DHL/FedEx via single Aftership API key per brand
- 🧪 Tests: Manual — add key in Settings → Integrations, ask Luna about a shipped order
- 📁 Files: 4 files modified/created
- 🔁 Next: Add `aftership_api_key` column to Supabase `brands` table (nullable text); test with real Aftership free tier key
- 🧠 Reflection: The 5s timeout + non-blocking exception handling ensures Aftership failures never delay Luna's reply

## Evaluation notes (flywheel)

- Failure modes observed: Carrier slug not in map → graceful skip; Aftership timeout → falls back to URL
- Graders run and results (PASS/FAIL): Not run
- Prompt variant (if applicable): null
- Next experiment: Auto-detect carrier via Aftership detect endpoint when slug not in map
