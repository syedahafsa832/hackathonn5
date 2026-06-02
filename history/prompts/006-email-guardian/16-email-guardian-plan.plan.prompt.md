---
id: 16
title: email-guardian-plan
stage: plan
date: 2026-05-15
surface: agent
model: claude-sonnet-4-6
feature: 006-email-guardian
branch: 006-email-guardian
user: aicoders123@gmail.com
command: /sp.plan
labels: [email-guardian, plan, ai-classification, quarantine, mistral]
links:
  spec: specs/006-email-guardian/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/006-email-guardian/plan.md
  - specs/006-email-guardian/research.md
  - specs/006-email-guardian/data-model.md
  - specs/006-email-guardian/quickstart.md
  - specs/006-email-guardian/contracts/email-guardian-openapi.yaml
tests:
  - "quickstart.md Scenario 1: Sales pitch blocked by AI classification"
  - "quickstart.md Scenario 2: Genuine support email passes all 5 layers"
  - "quickstart.md Scenario 3: Low-confidence email quarantined"
  - "quickstart.md Scenario 9: Classifier unavailable → quarantine (fail open)"
  - "quickstart.md Scenario 11: New brand inherits safe defaults"
---

## Prompt

/sp.plan

(No additional user input — execute planning workflow for 006-email-guardian spec)

## Response snapshot

Generated all Phase 0 (research) and Phase 1 (design) artifacts for feature 006-email-guardian.

**Phase 0 — Research (8 decisions documented):**

1. Mistral API client reused from customer_success_agent.py (OpenAI SDK compat) — no new packages
2. Guardian inserts after email_filter_service.evaluate() in email_poller.py ~line 121; only fires for emails that pass layers 1-3
3. New `email_quarantine` table (pending/promoted/discarded/expired states, 7-day expiry)
4. `email_filter_log` extended with `ai_classification` + `ai_confidence` columns (append-only audit)
5. Three new `system_settings` columns: `support_only_mode=true`, `confidence_threshold=0.75`, `auto_reply_enabled=true`
6. Lazy expiry: quarantine records bulk-expired on list query (no CRON infrastructure)
7. Promote creates a real ticket via direct supabase_insert mirroring poller pattern
8. `/quarantine` route added to App.jsx after `/actions`

**Phase 1 — Design artifacts:**
- `data-model.md`: migration SQL for 3 schema changes, entity relationships, state transitions
- `contracts/email-guardian-openapi.yaml`: 5 endpoints (GET/POST quarantine, GET/PATCH settings, GET filter-logs extended)
- `quickstart.md`: 11 integration test scenarios covering all 5 user stories + edge cases

**Constitution check**: All 12 gates pass. No violations. Highlights:
- No new packages (Constitution XI) — Mistral OpenAI SDK already in requirements.txt
- email_filter_service.py untouched (Constitution XII)
- All new endpoints use get_current_tenant / v1 JWT (Constitution X)
- Fail-open on classifier error (Constitution III)

**3-milestone delivery strategy:**
- M1: Safety (guardian service + poller integration — no UI)
- M2: Operator tooling (quarantine API + QuarantineQueue.jsx)
- M3: Settings + dashboard (Settings.jsx controls + widget update)

## Outcome

- ✅ Impact: Complete implementation plan for AI-powered email qualification — all research resolved, data model defined, API contracts specified, 11 test scenarios ready.
- 🧪 Tests: 11 quickstart scenarios covering classifier blocking, quarantine, promote, discard, fail-open, settings changes, and dashboard visibility.
- 📁 Files: plan.md, research.md, data-model.md, quickstart.md, contracts/email-guardian-openapi.yaml
- 🔁 Next prompts: /sp.tasks to generate the task breakdown
- 🧠 Reflection: The key architectural insight is that the guardian fires AFTER the rule-based filter — Mistral API is only called for emails that survive layers 1-3, minimizing cost. The fail-open invariant (classifier error → quarantine, not block) ensures a real customer is never silently dropped.

## Evaluation notes (flywheel)

- Failure modes observed: None — all research questions resolved from existing codebase patterns.
- Graders run and results (PASS/FAIL): Constitution check — all 12 gates PASS.
- Prompt variant (if applicable): null
- Next experiment (smallest change to try): Run /sp.tasks; ensure M1 tasks are independently deployable without M2/M3.
