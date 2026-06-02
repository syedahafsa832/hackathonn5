---
id: 15
title: email-guardian-spec
stage: spec
date: 2026-05-15
surface: agent
model: claude-sonnet-4-6
feature: 006-email-guardian
branch: 006-email-guardian
user: aicoders123@gmail.com
command: /sp.specify
labels: [email-guardian, spec, ai-classification, quarantine, support-only-mode]
links:
  spec: specs/006-email-guardian/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/006-email-guardian/spec.md
  - specs/006-email-guardian/checklists/requirements.md
tests:
  - "SC-001: Zero promotional emails create tickets when support_only_mode=true"
  - "SC-002: Support emails reach ticket queue within 90 seconds"
  - "SC-006: Every evaluated email has a guardian audit log entry"
---

## Prompt

/sp.specify

CRITICAL EMAIL SAFETY + CUSTOMER SUPPORT MODE REWORK

The current AI email system is behaving like a blind autoresponder instead of a real customer support employee.

Right now it replies to: promotional emails, newsletters, automated notifications, outreach emails, marketing blasts, random inbox messages.

This is unacceptable for production e-commerce usage.

[Full description: 5-layer filtering engine (Gmail category, sender reputation, header inspection, AI intent classification, confidence gate), support-only mode, quarantine queue, per-tenant settings, dashboard analytics, audit logging]

## Response snapshot

Created branch `006-email-guardian` and spec at `specs/006-email-guardian/spec.md`.

Feature scoped as additive to feature 005 (existing layers 1–3 + loop detection remain unchanged). New additions:

- **Layer 4 — AI Intent Classification**: Classifies email into customer_support / promotion / newsletter / outreach / spam / automation / unknown using the existing Mistral API. Only `customer_support` proceeds when `support_only_mode=true`.
- **Layer 5 — Confidence Gate**: Emails classified as `customer_support` with `confidence < threshold` are quarantined (not blocked, not auto-replied).
- **Support-Only Mode**: Per-tenant toggle, default=true. Safe production default for all new brands.
- **Quarantine Queue**: Operator-facing UI to promote quarantined emails to tickets or discard. Auto-expires after 7 days.
- **New Settings**: `confidence_threshold` (default 0.75), `support_only_mode` (default true), `auto_reply_enabled` (default true).
- **Updated Dashboard Widget**: Adds quarantined count, ai_classification reason, low_confidence reason.

5 user stories defined (US1-US5, P1-P3), all independently testable. 13 functional requirements, 3 key entities (GuardianDecision, QuarantineRecord, GuardianSettings), 7 measurable success criteria. All checklist items pass.

## Outcome

- ✅ Impact: Spec fully covers the AI-powered email qualification pipeline including quarantine and support-only mode defaults.
- 🧪 Tests: 5 independent test scenarios defined (one per user story). SC-001 through SC-007 measurable without implementation details.
- 📁 Files: specs/006-email-guardian/spec.md, specs/006-email-guardian/checklists/requirements.md
- 🔁 Next prompts: /sp.plan to generate architecture, data model, and API contracts
- 🧠 Reflection: Key distinction from feature 005: this spec introduces AI-powered classification (not just rule-based) and a quarantine state (middle ground between allow and block). The "fail open" invariant is preserved — classifier unavailability quarantines rather than drops emails.

## Evaluation notes (flywheel)

- Failure modes observed: None — spec has no NEEDS CLARIFICATION markers; all ambiguities resolved via documented assumptions.
- Graders run and results (PASS/FAIL): Checklist requirements.md — all 12 items PASS.
- Prompt variant (if applicable): null
- Next experiment (smallest change to try): Run /sp.clarify to verify no hidden ambiguities before planning.
