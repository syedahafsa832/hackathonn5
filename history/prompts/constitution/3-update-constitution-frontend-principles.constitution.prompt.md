---
id: "3"
title: "Update constitution frontend principles"
stage: "constitution"
date: "2026-04-13"
surface: "agent"
model: "minimax-m2.5"
feature: "none"
branch: "main"
user: "unknown"
command: "/sp.constitution"
labels: ["constitution", "frontend", "architecture"]
links:
  spec: "null"
  ticket: "null"
  adr: "null"
  pr: "null"
files:
  - ".specify/memory/constitution.md"
tests:
  - "No placeholder tokens remaining"
  - "Version incremented correctly"
---

## Prompt

Frontend must be rebuilt as a unified AI Operations Cockpit for Shopify brands.

Principles:

Event-Driven UI Only
UI must be built around system events, not pages or modules
No static dashboards or isolated feature pages
Single Source of Truth
All frontend data must originate from /api/events
No duplicate data sources (no separate inbox/actions/history APIs in UI layer)
Workflow-First Design
UI represents lifecycle of business operations:
Email → AI Decision → Action → Approval → Execution
Strict Separation of Concerns
UI = visualization only
Backend = decision + execution logic
AI = reasoning layer only
No SaaS Dashboard Patterns
Avoid admin panels, CRUD tables, or tab-heavy layouts
System must feel like a real-time operations control system
Real-Time First
UI must reflect live system state changes instantly
No manual refresh flows

## Response snapshot

Updated constitution to v2.1.0. Added new principle VIII: Frontend Architecture with 4 sub-principles (Event-Driven UI, Single Source of Truth, Workflow-First, Real-Time First). Updated IX. Product Philosophy to include "No SaaS Dashboard Patterns" rule. Version bumped from 2.0.0 to 2.1.0 (MINOR - new principle added).

## Outcome

- ✅ Impact: Constitution now includes frontend architecture principles
- 🧪 Tests: Manual validation of placeholder tokens
- 📁 Files: 1 modified (.specify/memory/constitution.md)
- 🔁 Next prompts: None
- 🧠 Reflection: Added 4 new frontend-focused sub-principles under VIII. Frontend Architecture, and "No SaaS Dashboard Patterns" to IX. Product Philosophy

## Evaluation notes (flywheel)

- Failure modes observed: None
- Graders run and results (PASS/FAIL): N/A
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): N/A