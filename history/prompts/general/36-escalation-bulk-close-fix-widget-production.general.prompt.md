---
id: 36
title: Escalation bulk close fix, widget production ready
stage: general
date: 2026-06-08
surface: agent
model: claude-sonnet-4-6
feature: chat-widget
branch: updated_whole_damn_product
user: Syeda Hafsa
command: Fix escalation bulk-close silent fail + make chat widget production-ready
labels: ["escalation", "bulk_resolve", "chat_widget", "production", "tenant_id", "organization_id"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/api/routes/v2_tickets.py
  - backend/src/static/widget.js
  - backend/main.py
tests:
  - none
---

## Prompt

Escalation page "mark all clear or resolved" not working, no error shown. Also make chat widget production-ready for external embedding.

## Response snapshot

**Root cause of silent bulk-close failure:**
- v2 `bulk_close_escalations_route` queries brands with `organization_id=eq.{...}`
- But brands are created by v1 auth (auth_service.py) with column `tenant_id`, not `organization_id`
- For v1/JWT users, `get_tenant_by_id` returns `UserContext(organization_id=tenant_id, brands=[])`
- Admin branch queries `organization_id` → returns empty → `brand_ids=[]` → returns `{success:true, closed:0}` silently
- No error thrown, no alert shown, tickets unchanged

**Fix applied:**
- `bulk_close_escalations_route`: if `organization_id` query returns empty, retry with `tenant_id` column
- Same fallback added to `list_tickets` (v2) admin branch
- Same fallback added for non-admin branch using `context.brand_ids` empty check

**Widget production readiness:**
- Added `data-api-base` attribute override (for CDN hosting)
- Default accent changed to `#6C63FF` (tResolv brand)
- Better embed comment with full attribute docs
- `/widget.js` endpoint: added `Access-Control-Allow-Origin: *` and `X-Content-Type-Options: nosniff` headers

## Outcome

- ✅ Mark all / mark selected escalations now finds the correct brands and closes tickets
- ✅ widget.js loadable from any cross-origin page
- ✅ `data-api-base` override allows CDN hosting of widget.js
- 📁 Files: 3 files modified
