---
id: 17
title: email-guardian-tasks
stage: tasks
date: 2026-05-16
surface: agent
model: claude-sonnet-4-6
feature: 006-email-guardian
branch: 005-email-filter-loop
user: aicoders123@gmail.com
command: /sp.tasks
labels: [email-guardian, tasks, ai-classification, quarantine, mistral, task-breakdown]
links:
  spec: specs/006-email-guardian/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/006-email-guardian/tasks.md
tests:
  - "quickstart.md Scenario 1: Sales pitch blocked by AI classification"
  - "quickstart.md Scenario 2: Genuine support email passes all 5 layers"
  - "quickstart.md Scenario 3: Low-confidence email quarantined"
  - "quickstart.md Scenario 4: Promote quarantined email to ticket"
  - "quickstart.md Scenario 5: Discard quarantined email"
  - "quickstart.md Scenario 6: Support-only mode blocks non-support"
  - "quickstart.md Scenario 7: Support-only mode disabled, non-support proceeds"
  - "quickstart.md Scenario 8: auto_reply_enabled=false creates ticket without AI reply"
  - "quickstart.md Scenario 9: Classifier unavailable quarantines (fail open)"
  - "quickstart.md Scenario 10: Dashboard widget shows quarantine count"
  - "quickstart.md Scenario 11: New brand inherits safe defaults"
---

## Prompt

/sp.tasks

(No additional user input — execute task generation workflow for 006-email-guardian spec)

## Response snapshot

Generated `specs/006-email-guardian/tasks.md` with 24 tasks across 8 phases for the Email Guardian feature.

**Phase 1 (T001)**: Create `backend/migrations/012_email_guardian_schema.sql`

**Phase 2 — Foundational (T002–T004)**:
- T002: `email_guardian_service.py` skeleton with `GuardianResult` dataclass and `GUARDIAN_ALLOW` sentinel
- T003: `_load_settings(brand_id)` with multi-tenant fallback to global defaults
- T004: Extend `v2_email_filter.py` defaults loader with 3 new guardian fields

**Phase 3 — US1 P1 MVP (T005–T010)**: AI Classification Blocks Non-Support Emails
- T005 [P]: `_classify_email()` calling Mistral OpenAI-compat API
- T006 [P]: `_create_quarantine_record()` writing to `email_quarantine`
- T007: `evaluate()` orchestrating full guardian decision (blocked/quarantined/allowed)
- T008: `log_guardian_decision()` writing AI fields to `email_filter_log`
- T009: Integrate guardian into `email_poller.py` after filter_service layers 1–3
- T010: Pass `auto_reply_enabled` flag through poller → message_processor

**Phase 4 — US2 P1 (T011–T012)**: Support-Only Mode as Production Default
- T011: Update `EmailFilterSettingsResponse` and `EmailFilterSettingsPatch` Pydantic models
- T012: Update PATCH upsert logic for 3 new settings fields

**Phase 5 — US3 P2 (T013–T018)**: Quarantine Queue API + UI
- T013: `GET /quarantine` with lazy expiry
- T014: `POST /quarantine/{id}/promote` creates real ticket
- T015: `POST /quarantine/{id}/discard`
- T016: Register quarantine router in `main.py`
- T017 [P]: `QuarantineQueue.jsx` page
- T018: Add `/quarantine` route in `App.jsx`

**Phase 6 — US4 P2 (T019)**: Settings UI Controls (confidence_threshold, support_only_mode, auto_reply_enabled)

**Phase 7 — US5 P3 (T020–T021)**: Guardian Analytics Dashboard
- T020 [P]: Extend `GET /filter-logs?summary=true` with `total_quarantined`
- T021 [P]: Update `FilteredEmailsWidget.jsx` with quarantine count + "Review" link

**Phase 8 — Polish (T022–T024)**: Nav link, tenant isolation check, quickstart validation

**Milestone grouping**:
- M1 (Phases 1–4, T001–T012): Safety — ships without UI
- M2 (Phases 5–6, T013–T019): Operator tooling + settings UI
- M3 (Phase 7, T020–T021): Dashboard analytics

## Outcome

- ✅ Impact: Complete task breakdown for 006-email-guardian — 24 immediately-executable tasks covering all 5 user stories (US1-US5) across 3 milestones.
- 🧪 Tests: 11 quickstart.md scenarios mapped to phases; M1 validates with Scenarios 1, 2, 6, 9, 11; M2 validates with Scenarios 3, 4, 5, 7, 8; M3 validates with Scenario 10.
- 📁 Files: specs/006-email-guardian/tasks.md
- 🔁 Next prompts: /sp.implement to execute T001–T024; apply SQL migration first (backend/migrations/012_email_guardian_schema.sql)
- 🧠 Reflection: Phases 3 and 4 (US1 + US2) can run in parallel since they modify different files — T005/T006 are independent within US1. The guardian service wraps the existing filter service without touching it, preserving the feature 005 invariants. The fail-open pattern (any exception → GUARDIAN_ALLOW) must be preserved at the evaluate() level.

## Evaluation notes (flywheel)

- Failure modes observed: None — all design documents were available; task descriptions are concrete enough for immediate execution.
- Graders run and results (PASS/FAIL): Constitution check inherited from plan.md — all 12 gates PASS; task format validation — all 24 tasks follow [ID] [P?] [Story?] file-path format.
- Prompt variant (if applicable): null
- Next experiment (smallest change to try): Run /sp.implement starting at T001; confirm migration applies cleanly in Supabase SQL editor before executing T002.
