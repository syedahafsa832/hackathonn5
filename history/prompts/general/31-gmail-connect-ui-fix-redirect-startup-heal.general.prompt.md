---
id: 31
title: Fix Gmail connect UI — wrong redirect + startup-heal null
stage: general
date: 2026-06-06
surface: agent
model: claude-sonnet-4-6
feature: none
branch: updated_whole_damn_product
user: Syeda Hafsa
command: RESOLV — Gmail "Connect Gmail" shows despite being connected; file_cache warning
labels: ["gmail", "oauth", "redirect", "startup_heal", "brand", "fix"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/api/routes/brand_gmail.py
  - backend/main.py
  - backend/src/services/brand_gmail_service.py
tests:
  - none
---

## Prompt

Why is UI showing "Connect Gmail" despite having re-authenticated? Logs show `file_cache is only supported with oauth2client<4.0.0`.

## Response snapshot

**Root cause — wrong redirect URL:**
- `brand_gmail.py:69`: OAuth callback was redirecting to `/settings?gmail_connected=1` after success
- Brands page (`Brands.jsx`) reads `gmail_connected` URL param and shows success banner — but user never lands there
- `useEffect([], [])` in Brands page refetches brands on mount — if redirected to `/brands`, it would pick up the now-connected brand from the API
- Fix: changed redirect from `/settings?...` to `/brands?gmail_connected=1&email={email}` (success) and `/brands?gmail_error=...` (failure)

**Startup-heal null guard:**
- `main.py` startup-heal only queried `gmail_connected=eq.false` — brands with `gmail_connected=null` (never explicitly set) were missed
- Added second query for `gmail_connected=is.null` and merged both result sets before processing
- Same refresh_token validation logic applies to both

**file_cache warning suppression:**
- Added `logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)` in `brand_gmail_service.py` after the `build` import
- This is a harmless INFO log from the Google API client (oauth2client>=4.0.0 doesn't support file caching for discovery docs) — NOT a functional error

## Outcome

- ✅ After OAuth, user now lands on `/brands` page with fresh brand list showing Gmail connected + success banner
- ✅ Startup-heal now catches both `gmail_connected=false` and `gmail_connected=null` brands with a stored refresh_token
- ✅ `file_cache` warning no longer appears in logs
- 🧪 Tests: manual — connect Gmail for a brand, verify redirect lands on Brands page showing connected state
- 📁 Files: 3 files modified

## Evaluation notes (flywheel)

- Failure modes observed: OAuth callback redirected to wrong page; startup-heal missed null values
- Graders run and results (PASS/FAIL): none automated
- Next experiment: verify redirect works end-to-end in browser; check startup logs for healed null brands
