---
id: 12
title: Email Filter Loop Prevention Plan
stage: plan
date: 2026-05-15
surface: agent
model: claude-sonnet-4-6
feature: 005-email-filter-loop
branch: 005-email-filter-loop
user: aicoders123@gmail.com
command: /sp.plan
labels: ["email-filtering", "loop-prevention", "planning", "pipeline", "data-model"]
links:
  spec: specs/005-email-filter-loop/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/005-email-filter-loop/plan.md
  - specs/005-email-filter-loop/research.md
  - specs/005-email-filter-loop/data-model.md
  - specs/005-email-filter-loop/quickstart.md
  - specs/005-email-filter-loop/contracts/email-filter-openapi.yaml
tests:
  - null
---

## Prompt

/sp.plan

(no additional arguments — plan the 005-email-filter-loop feature from its spec)

## Response snapshot

Completed full Phase 0 + Phase 1 planning for the Email Filtering & Loop Prevention feature:

**Phase 0 (Research)** — 8 technical decisions resolved in `research.md`:
1. Synchronous filter placement in email poller (before ticket creation)
2. Gmail category detection via existing `labelIds` field in Gmail API response
3. Filter config stored in new columns on `system_settings` table (not a new table)
4. New append-only `email_filter_log` table for filter decision logging
5. Loop detection in poller; `auto_reply_count` increment in message_processor after confirmed send
6. No new packages — stdlib `re`/`json`/`logging` sufficient
7. All new endpoints use `Depends(get_current_tenant)` (v1 JWT — Constitution Principle X)
8. Whitelist bypasses sender-pattern + Gmail category but NOT header checks

**Phase 1 (Design & Contracts)** — four artifacts:

`data-model.md` — Three schema changes:
- New `email_filter_log` table (7 columns + index on `brand_id, created_at DESC`)
- Extended `system_settings`: 5 new columns (`blocked_domains`, `whitelisted_domains`, `max_auto_replies`, `promotion_filter_enabled`, `loop_protection_enabled`)
- Extended `tickets`: 4 new columns (`email_category`, `sender_type`, `loop_risk`, `auto_reply_count`)
- Full migration SQL at `backend/migrations/011_email_filter_schema.sql`

`quickstart.md` — 8 integration scenarios covering the golden path (real support email passes), filter layers (promotional, header, Gmail category), loop detection, whitelist bypass, dashboard widget, and 5 edge cases

`contracts/email-filter-openapi.yaml` — 3 endpoints:
- `GET /api/v1/settings/email-filter` — returns current filter settings
- `PATCH /api/v1/settings/email-filter` — partial update, changes effective within 60s
- `GET /api/v1/filter-logs?summary=true&days=7` — aggregated dashboard widget data or paginated log list

`plan.md` — Full implementation plan with:
- Constitution check table (all 8 gates PASS)
- Concrete source code layout (new files: `email_filter_service.py`, `v2_email_filter.py`, migration; modified: `email_poller.py`, `message_processor.py`, Settings UI, Dashboard)
- Two-milestone implementation strategy (Milestone 1: safety-critical P1; Milestone 2: UI/dashboard P2/P3)
- Fallback: filter exception → `decision: allowed` (never block real customer on filter crash)

## Outcome

- ✅ Impact: Complete architecture for production-grade email filtering; resolves the overnight AI reply loop incident by design
- 🧪 Tests: No code yet; 8 quickstart scenarios document acceptance criteria for each filter layer
- 📁 Files: specs/005-email-filter-loop/plan.md, research.md, data-model.md, quickstart.md, contracts/email-filter-openapi.yaml
- 🔁 Next prompts: /sp.tasks to generate the task breakdown for Milestone 1 and Milestone 2
- 🧠 Reflection: Synchronous filter placement was the key architectural decision — it is the only design that guarantees no ticket is created before filtering completes, at the cost of zero additional complexity

## Evaluation notes (flywheel)

- Failure modes observed: None — all 8 research decisions were clear from the spec; no NEEDS CLARIFICATION items remained
- Graders run and results (PASS/FAIL): Constitution check PASS (all 8 gates); data-model validated against spec entities
- Prompt variant (if applicable): null
- Next experiment (smallest change to try): Run /sp.tasks with explicit milestone grouping instruction to ensure Milestone 1 tasks are fully independent of Milestone 2 (dashboard/UI) so safety fixes can ship without waiting for UI polish
