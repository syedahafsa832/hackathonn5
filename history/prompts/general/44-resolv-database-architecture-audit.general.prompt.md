---
id: 44
title: Resolv Database Architecture Audit
stage: general
date: 2026-07-22
surface: agent
model: GPT-5
feature: none
branch: main
user: syedahafsa832
command: database architecture audit
labels: ["resolv", "supabase", "database-architecture", "multi-tenancy", "security", "scalability"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
 - history/prompts/general/44-resolv-database-architecture-audit.general.prompt.md
tests:
 - Read-only live Supabase schema, foreign-key, RLS, security-advisor, and performance-advisor audit
 - Local migration and schema declaration review
---

## Prompt

You are a Principal Software Architect.

I am building Resolv, an AI customer support employee for Shopify.

I already have a working backend with many tables.

I do NOT want to add new features.

I want to improve my architecture.

Your job is NOT to write code.[$supabase](app://asdk_app_69d3e5ee6a708191baa733f7b8931995)

Your job is to understand my current database and identify:

1. Duplicate concepts 
2. Overlapping tables
3. Tables that should be merged
4. Missing business concepts
5. Bad naming
6. Missing relationships
7. Production risks
8. Scalability risks

For every suggestion explain WHY.

Do not generate SQL.

Wait until we agree on the architecture before suggesting implementation.

## Response snapshot

Completed a read-only audit of the live Resolv Supabase project and local migrations. The review found incompatible tenancy models, duplicate action/catalog/knowledge concepts, missing referential integrity, and critical RLS exposure; recommendations are architectural only and contain no SQL or implementation instructions.

## Outcome

- ✅ Impact: Produced an evidence-backed database architecture assessment and a proposed target conceptual model for discussion before implementation.
- 🧪 Tests: Read-only metadata, linter, and migration inspections; no code tests were applicable.
- 📁 Files: Created this required prompt-history record only.
- 🔁 Next prompts: Agree the canonical tenancy boundary and action/conversation models before any migration or code plan.
- 🧠 Reflection: A canonical ownership model must be selected before table consolidation, because every other relationship and security policy depends on it.

## Evaluation notes (flywheel)

- Failure modes observed: One read-only row-count query referenced a non-existent legacy table and was safely rejected; the audit continued using live metadata. No schema or data was changed.
- Graders run and results (PASS/FAIL): PASS — live Supabase schema and advisor evidence inspected; PASS — no implementation or SQL generated.
- Prompt variant (if applicable): none
- Next experiment (smallest change to try): Validate the proposed ownership hierarchy with product stakeholders using one real merchant, one multi-brand merchant, and one internal support-user scenario.
