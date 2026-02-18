---
id: 1
title: AI Customer Success Constitution
stage: constitution
date: 2026-02-03
surface: agent
model: claude-sonnet-4-5-20250929
feature: none
branch: master
user: default_user
command: /sp.constitution
labels: [constitution, ai, customer-success, principles]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
 - .specify/memory/constitution.md
tests:
 - none
---

## Prompt

Create development principles for building a production-grade AI Customer Success Digital FTE with these priorities:
1. Multi-channel consistency - responses adapt to channel (email=formal, WhatsApp=concise, web=semi-formal)
2. Cross-channel customer identification - same customer recognized across all channels
3. Zero data loss - all messages stored, Kafka for reliability
4. Escalation safety - always escalate pricing, legal, refunds, angry customers
5. Channel-appropriate formatting - respect length limits (email=500 words, WhatsApp=300 chars, web=300 words)
6. Database-first CRM - PostgreSQL is our CRM, no external integrations needed
7. Production readiness - proper error handling, retry logic, monitoring
8. Testing rigor - unit tests, integration tests, channel-specific E2E tests

## Response snapshot

Updated the constitution file with 8 core principles covering multi-channel consistency, cross-channel customer identification, zero data loss, escalation safety, channel-appropriate formatting, database-first CRM, production readiness, and testing rigor. Also included sections on technology stack, security requirements, performance standards, development workflow, and governance.

## Outcome

- ✅ Impact: Created comprehensive constitution for AI Customer Success Digital FTE with 8 core principles
- 🧪 Tests: No tests required for constitution update
- 📁 Files: Updated .specify/memory/constitution.md with new principles
- 🔁 Next prompts: Implement features following these constitutional principles
- 🧠 Reflection: Constitution now provides clear guidance for AI Customer Success development

## Evaluation notes (flywheel)

- Failure modes observed: None
- Graders run and results (PASS/FAIL): N/A
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): Begin feature development aligned with these principles
