---
id: 20
title: Fix Button Disabled-State Bug + Restart API Container
stage: general
date: 2026-06-03
surface: agent
model: claude-sonnet-4-6
feature: none
branch: 006-email-guardian
user: Syeda Hafsa
command: Buttons don't do anything when clicked — diagnose and fix
labels: ["frontend","backend","docker","disabled-state","cancel","refund","orderPanel"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - ai-ops-console/src/pages/TicketDetail.jsx
tests:
  - manual: click Update Address → shows "✓ Queued" → click Cancel → confirm dialog → button works (not disabled)
  - manual: click Reship → shows "✓ Queued" → click Refund → confirm dialog → button works (not disabled)
  - manual: click Cancel → confirm → shows "Cancelling..." → success → page reloads
---

## Prompt

Same "wire Cancel/Refund buttons" spec re-submitted. Buttons visually present but click does nothing.

## Response snapshot

### Root causes identified

**Bug 1 — disabled state cross-contamination (frontend)**

`disabled={!!actionLoading || !!stagingAction}` on the Cancel/Refund buttons.

If the user had previously clicked "Update Address" or "Reship" in the same page session, `stagingAction` becomes `'ADDRESS_CHANGE'` or `'done:ADDRESS_CHANGE'` (non-empty truthy string). This disabled ALL four buttons including Cancel and Refund — making them appear to "do nothing" when clicked (they were actually just disabled).

Fix: execute buttons (Cancel/Refund) now use `disabled={!!actionLoading}` only.
Staging buttons (Address/Reship) use `disabled={!!actionLoading || stagingAction === type}` — only disable themselves while in-flight.

**Bug 2 — stale backend container**

`customer-success-fte-api` was running gunicorn WITHOUT `--reload`. docker-compose mounts `./backend/src:/app/src` (volume) so file changes appear on disk inside the container, but gunicorn caches Python modules at startup. Changes to `v2_tickets.py` (cancel/refund endpoints) and `v2_actions.py` were never reloaded.

Fix: `docker restart customer-success-fte-api` — container restarted, confirmed healthy.

## Outcome

- ✅ Cancel/Refund buttons no longer disabled after staging an address change
- ✅ Backend container restarted and healthy — new route handlers loaded
- 🧪 Tests: Manual — see test checklist above
- 📁 Files: 1 file modified (TicketDetail.jsx)

## Evaluation notes (flywheel)

- Root cause was subtle: `!!stagingAction` where stagingAction is a string like 'done:ADDRESS_CHANGE'. `!!''` = false but `!!'done:ADDRESS_CHANGE'` = true.
- Gunicorn no-reload + volume mounts is a silent trap: files change on disk but process imports are stale.
- Next: add `--reload` to gunicorn dev command or document "restart after backend changes" in runbook.
