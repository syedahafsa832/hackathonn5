---
id: 7
title: Resolv MVP Tasks Generated
stage: tasks
date: 2026-05-13
surface: agent
model: claude-sonnet-4-6
feature: 004-resolv-completion
branch: 004-resolv-completion
user: Syeda Hafsa
command: /sp.tasks
labels: ["tasks","auth","worker","tickets","dashboard","email","knowledge-base","onboarding","threading","notifications"]
links:
  spec: specs/004-resolv-completion/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/004-resolv-completion/tasks.md
tests:
  - null
---

## Prompt

Generate tasks from the plan above. Each task must:

Have one clear deliverable
Reference specific files to edit (not just "fix the backend")
Include a verification step (how to confirm it works)
Be completable independently without depending on unfinished tasks above it

Group tasks into these milestones:

Milestone 1: Core fixes (auth, worker, ticket fields, dashboard, email sending) — product must work end-to-end after this
Milestone 2: Missing features (knowledge base UI, onboarding, threading, notifications) — product is complete after this

## Response snapshot

Generated 25 tasks across 10 phases in `specs/004-resolv-completion/tasks.md`.

**Milestone 1 — Critical fixes (T001–T012)**:
- Phase 1 (T001–T003): Auth middleware v1 token fallback in `auth_middleware.py`
- Phase 2 (T004–T005): Worker per-brand isolation in `email_poller.py`
- Phase 3 (T006): Ticket channel null default in `tickets.py`
- Phase 4 (T007–T009): Dashboard stats rewrite in `services.js`
- Phase 5 (T010–T012): Approve-ai email send in `v2_tickets.py`

**Milestone 2 — Feature completions (T013–T025)**:
- Phase 6 (T013–T017): Knowledge base tab in `Settings.jsx`
- Phase 7 (T018–T019): Onboarding redirect in `Dashboard.jsx`
- Phase 8 (T020): Email threading in `email_poller.py`
- Phase 9 (T021–T023): Browser notifications in `Dashboard.jsx`
- Phase 10 (T024–T025): Polish and end-to-end verification

## Outcome

- ✅ Impact: 25 tasks generated covering all 9 spec items; T001 is the critical unblocking task
- 🧪 Tests: Manual verification steps embedded per task (curl commands, UI interactions)
- 📁 Files: specs/004-resolv-completion/tasks.md
- 🔁 Next prompts: /sp.implement to execute T001 (auth middleware fix) first
- 🧠 Reflection: Tasks strictly ordered — T001 (auth) unblocks T010-T012 (v2 routes) and T013-T023 (UI features requiring auth). Worker tasks (T004-T005) and ticket field (T006) are independent of auth and can run in parallel. Milestone 1 makes product functional; Milestone 2 makes it complete.

## Evaluation notes (flywheel)

- Failure modes observed: None — task generation followed plan exactly
- Graders run and results (PASS/FAIL): constitution check — all tasks respect file boundaries and no-new-packages constraint
- Prompt variant (if applicable): N/A
- Next experiment: implement T001 first and verify with curl before touching any other task
