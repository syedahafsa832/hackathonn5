---
id: 11
title: Email Filter Loop Prevention Spec
stage: spec
date: 2026-05-15
surface: agent
model: claude-sonnet-4-6
feature: 005-email-filter-loop
branch: 005-email-filter-loop
user: aicoders123@gmail.com
command: /sp.specify
labels: ["email-filtering", "loop-prevention", "safety", "pipeline"]
links:
  spec: specs/005-email-filter-loop/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/005-email-filter-loop/spec.md
  - specs/005-email-filter-loop/checklists/requirements.md
tests:
  - null
---

## Prompt

CRITICAL EMAIL FILTERING + LOOP PREVENTION SYSTEM

The current AI email agent is dangerously replying to promotional emails and entering AI-to-AI infinite reply loops.

Example failure:
- Promotional/newsletter emails were processed
- The system auto-replied
- The sender also had an AI auto-responder
- Both agents started continuously replying to each other
- The system sent ~20 unnecessary replies overnight

This is unacceptable for production.

We need to redesign the email ingestion + reply pipeline to behave like a real customer support employee, not a blind autoresponder.

GOAL: Only process REAL customer support conversations. Ignore newsletters, promotions, marketing blasts, automated notifications, and bot-generated emails.

NEW EMAIL FILTERING REQUIREMENTS: The system MUST classify emails BEFORE ticket creation via a new module backend/src/services/email_filter_service.py.

FILTERING RULES: Gmail category detection, automated sender detection (noreply/newsletter prefixes and blocked domains), auto-reply header detection, AI loop prevention with MAX_THREAD_REPLIES=2, promotional content heuristics, human support intent detection.

DATABASE CHANGES: tickets gains email_category, sender_type, loop_risk, auto_reply_count, filtered_reason. logs gains filter_decision, loop_detection_reason.

ADMIN SETTINGS: Settings page allows blacklist/whitelist domains, max auto replies, enable/disable promotion filtering and loop protection.

MONITORING: Dashboard widget "Filtered Emails" showing ignored newsletters, blocked automated emails, prevented AI loops, spam prevented.

IMPLEMENTATION PRIORITY: email_filter_service.py → integrate before ticket creation → loop detection → database tracking → dashboard visibility → admin controls.

## Response snapshot

Created branch `005-email-filter-loop` and wrote a complete feature specification at `specs/005-email-filter-loop/spec.md` with:

- 5 prioritized user stories (US1/US2/US3 all P1 — safety critical; US4 P2 admin config; US5 P3 dashboard)
- 19 functional requirements covering pre-ticket filtering, loop prevention, admin configuration, and dashboard visibility
- 3 key entities: FilterDecision (log), EmailFilterSettings (per-tenant config), and extended Ticket fields
- 7 measurable success criteria (zero false tickets, loops ≤2 exchanges, <1% false-positive rate, 60s dashboard refresh, etc.)
- Clear scope boundary: email channel only, heuristic filtering only (no ML), no Gmail-level blocking
- Edge cases: malformed headers, whitelist/blocklist conflict, missing thread IDs, max_auto_replies=0

Checklist created at `specs/005-email-filter-loop/checklists/requirements.md` — all items pass.

## Outcome

- ✅ Impact: Spec defines production-grade email filtering and loop prevention — resolves the ~20-reply overnight incident
- 🧪 Tests: No code changes yet; acceptance scenarios documented for each user story
- 📁 Files: specs/005-email-filter-loop/spec.md, specs/005-email-filter-loop/checklists/requirements.md
- 🔁 Next prompts: /sp.plan to design email_filter_service.py architecture and pipeline integration
- 🧠 Reflection: User spec was comprehensive — no clarifications needed; all filtering rules mapped directly to FR items

## Evaluation notes (flywheel)

- Failure modes observed: None — spec derived cleanly from the detailed user description
- Graders run and results (PASS/FAIL): Checklist validation PASS (all 14 items)
- Prompt variant (if applicable): null
- Next experiment (smallest change to try): Run /sp.plan with explicit constraint to keep filtering synchronous in the email poller (not async background job) to minimize latency between email arrival and ticket creation
