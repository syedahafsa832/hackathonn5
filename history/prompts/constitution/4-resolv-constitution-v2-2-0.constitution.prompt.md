---
id: 4
title: Resolv Constitution v2.2.0
stage: constitution
date: 2026-05-13
surface: agent
model: claude-sonnet-4-6
feature: none
branch: main
user: Syeda Hafsa
command: /sp.constitution
labels: ["constitution","auth","engineering-discipline","feature-stability","resolv"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - .specify/memory/constitution.md
tests:
  - null
---

## Prompt

Project: Resolv — AI support employee for Shopify brands.
Stack: FastAPI backend (hack5/), React Vite admin UI (ai-ops-console/), React web-form UI (web-form/).
Principles:

Never break working features. If a route works, only touch it if the task explicitly requires it.
One auth system only. All routes use v1 JWT tokens (Authorization: Bearer). Do not mix v1 and v2 auth.
API-first. Every UI feature must have a working backend endpoint before the frontend is built.
No mock data in production code. All UI components fetch from real API endpoints.
Confidence gating is sacred. No financial action (refund, cancel, modify) executes without an approved action record in the database.
Error states are required. Every API call in the UI must handle loading, error, and empty states explicitly.
Do not install new packages unless absolutely necessary. Use what is already in requirements.txt and package.json.

## Response snapshot

Updated `.specify/memory/constitution.md` from v2.1.0 to v2.2.0:
- Renamed project from "AI Customer Success Digital FTE" to "Resolv"
- Added Principle X: Authentication Integrity — v1 JWT only, no mixing auth systems
- Added Principle XI: Engineering Discipline — API-first, no mock data, dependency minimalism, explicit error states
- Added Principle XII: Feature Stability — never break working features, scope changes strictly to task
- Strengthened Principle IV: confidence gating now explicitly requires approved action record in DB before execution
- Updated Last Amended date to 2026-05-13

## Outcome

- ✅ Impact: Constitution now codifies the 7 operational principles provided by the team, preventing recurring issues (dual auth, mock data, breaking working routes)
- 🧪 Tests: N/A — governance document
- 📁 Files: .specify/memory/constitution.md updated
- 🔁 Next prompts: /sp.adr for the dual auth system decision; update plan-template.md and tasks-template.md to reference new principles
- 🧠 Reflection: The three new principles (X, XI, XII) directly address bugs encountered during the current session — auth token mismatch, services.js pointing to wrong backend, port conflicts. Encoding these as principles prevents recurrence.

## Evaluation notes (flywheel)

- Failure modes observed: none
- Graders run and results (PASS/FAIL): N/A
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): propagate principles X-XII into tasks-template.md acceptance criteria checklist
