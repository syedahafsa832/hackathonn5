---
id: 32
title: Auto default brand on register and startup heal
stage: general
date: 2026-06-07
surface: agent
model: claude-sonnet-4-6
feature: none
branch: updated_whole_damn_product
user: Syeda Hafsa
command: RESOLV — auto-create default brand on registration so Gmail connect works immediately
labels: ["brand", "registration", "startup_heal", "gmail_connect", "onboarding", "fix"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/services/auth_service.py
  - backend/main.py
tests:
  - none
---

## Prompt

When a new user registers, automatically create a default brand so dashboard is not empty and Gmail connect works immediately. "Failed to start Gmail connection. Make sure a brand exists first." error should disappear.

## Response snapshot

**Status of registration auto-brand:**
- `auth_service.py:116-129` already creates a default brand on registration (added in a prior session)
- Was missing `gmail_connected: False` — could result in null in DB

**Changes made:**
- `auth_service.py`: Added `"gmail_connected": False` to the default brand insert payload
- `auth_service.py`: Added `.title()` to brand name (hafsa → Hafsa)

**Startup-heal step 5 (already existed):**
- `main.py` startup-heal step 5 loops all active tenants, creates a default brand if none found
- Catches tenants who registered before the auto-brand code was added

**Root cause of "Failed to start Gmail connection":**
- `_get_tenant_brand_async` queries `is_active IS TRUE` — null value makes brand invisible
- Step 5 creates a fresh default brand with explicit `is_active: True` and `gmail_connected: False` on next restart

## Outcome

- ✅ New registrations: brand created with explicit gmail_connected=False
- ✅ Existing tenants without brand: healed on next backend restart
- ✅ "Failed to start Gmail connection" disappears once brand exists
- 🧪 Tests: restart backend, check STARTUP-HEAL logs, verify Settings → Connect Gmail returns auth URL
- 📁 Files: auth_service.py modified; main.py step 5 already existed
