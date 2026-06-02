---
id: 6
title: Resolv MVP Implementation Plan
stage: plan
date: 2026-05-13
surface: agent
model: claude-sonnet-4-6
feature: 004-resolv-completion
branch: 004-resolv-completion
user: Syeda Hafsa
command: /sp.plan
labels: ["plan","auth","tickets","worker","email","knowledge-base","onboarding","threading","notifications","fastapi","react"]
links:
  spec: specs/004-resolv-completion/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/004-resolv-completion/plan.md
  - specs/004-resolv-completion/research.md
  - specs/004-resolv-completion/data-model.md
  - specs/004-resolv-completion/quickstart.md
  - specs/004-resolv-completion/contracts/resolv-completion-openapi.yaml
tests:
  - null
---

## Prompt

Backend: FastAPI, Python, Supabase (PostgreSQL with pgvector), deployed with Docker.
Admin UI: React 18, Vite, axios for API calls, react-router-dom v6, lucide-react for icons. No Tailwind. No component libraries.
Web Form: React 18, Vite, same conventions as admin UI.
Auth: Single JWT system. v1 tokens issued by POST /api/v1/auth/login. Token stored in localStorage as resolv_token. All API calls include Authorization: Bearer {token}. Fix v2 routes to accept v1 tokens — do not build a new auth system.
Implementation order — strictly follow this:
1. Fix auth middleware (unblocks everything else)
2. Fix background worker crash
3. Fix ticket list API (channel + sender fields)
4. Fix dashboard stats (real data)
5. Fix approve-ai and respond email sending
6. Build knowledge base UI
7. Build onboarding wizard
8. Fix email threading
9. Add browser notifications
Database: Do not change schema unless a task explicitly requires it. Use existing tables.
File boundaries: Backend changes only in hack5/ — Admin UI changes only in ai-ops-console/src/ — Web form changes only in web-form/src/ — Do not move or rename existing files

## Response snapshot

Generated full implementation plan at `specs/004-resolv-completion/plan.md`.

**Phase 0 (Research)**: All 9 items researched. Key findings:
- Item A: v1 token sub=tenant_id but middleware queries users table → add fallback to tenants table
- Item B: getStats() calls non-existent /admin/* routes → replace with /api/tickets + /api/v1/actions/stats
- Item D: Worker already has outer try/except → harden inner per-brand loop only
- Item E: approve-ai endpoint never calls send_email → add Gmail send call
- Items F/G/I: Backend endpoints exist; only UI wiring needed
- Item H: gmail_thread_id is captured but not used for ticket dedup

**Phase 1 (Design)**: Detailed design produced for all 9 items including exact code patterns,
file locations, and acceptance criteria. Data model confirmed — no schema changes required.
OpenAPI contract written for all affected endpoints.

**ADR suggested**: dual-auth-coexistence strategy for v1 tokens shadowing v2 Supabase Auth.

## Outcome

- ✅ Impact: Complete implementation plan ready. Auth fix (Item A) is the unblocking dependency.
- 🧪 Tests: Manual verification steps in quickstart.md for all 9 items
- 📁 Files: plan.md, research.md, data-model.md, quickstart.md, contracts/resolv-completion-openapi.yaml
- 🔁 Next prompts: /sp.tasks to generate actionable task list; then /sp.implement
- 🧠 Reflection: All 9 items have narrow, precise changes. Item A is critical path — without it
  Items E, F, G are blocked (v1 token can't access the routes they need). Item D required no
  backend change — already handled.

## Evaluation notes (flywheel)

- Failure modes observed: Item D appeared broken (worker crash) but was already fixed in code
- Graders run and results (PASS/FAIL): constitution check — all principles pass
- Prompt variant (if applicable): N/A
- Next experiment: implement Item A first and verify with curl before touching any other item
