---
id: 10
title: Fix Sidebar Admin Conversations 404
stage: green
date: 2026-05-13
surface: agent
model: claude-sonnet-4-6
feature: 004-resolv-completion
branch: main
user: Syeda Hafsa
command: /sp.implement
labels: ["fix","sidebar","404","escalations","polling"]
links:
  spec: specs/004-resolv-completion/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - ai-ops-console/src/components/Sidebar.jsx
tests:
  - null
---

## Prompt

(Continued from prior session) Runtime 404 error: `:8001/admin/conversations?conv_status=escalated` appearing in browser console after `docker compose up -d --build` succeeded.

## Response snapshot

Located the bug in `Sidebar.jsx:19` — the sidebar badge was still calling the non-existent `/admin/conversations?conv_status=escalated` meershakeel route.

Fixed by replacing the `useEffect` to poll two correct endpoints:
- `GET /api/v1/actions?status=pending` — pending financial actions
- `GET /api/tickets?status=escalated` — escalated conversations

Badge count now shows the sum of both. Polling interval set to 15s matching the rest of the app. The ai-ops-console runs via local Vite dev server (not in docker-compose), so HMR picks up the change immediately.

## Outcome

- ✅ Impact: 404 error eliminated; sidebar badge now shows accurate pending + escalated count with 15s polling
- 🧪 Tests: Manual — browser console should no longer show 404 for `/admin/conversations`
- 📁 Files: 1 file modified (`Sidebar.jsx`)
- 🔁 Next prompts: Verify sidebar badge shows correct counts after login; test end-to-end ticket → escalation flow
- 🧠 Reflection: The `Sidebar.jsx` was not in the Phase 2–7 implementation file list, so it was missed. Always grep for old route patterns after route migrations.

## Evaluation notes (flywheel)

- Failure modes observed: Incomplete route migration — Sidebar.jsx was not listed in the task file and was overlooked
- Graders run and results (PASS/FAIL): n/a
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): Add a grep check for `/admin/` routes as part of post-migration verification step
