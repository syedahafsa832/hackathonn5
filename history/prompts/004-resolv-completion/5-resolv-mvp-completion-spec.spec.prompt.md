---
id: 5
title: Resolv MVP Completion Spec
stage: spec
date: 2026-05-13
surface: agent
model: claude-sonnet-4-6
feature: 004-resolv-completion
branch: 004-resolv-completion
user: Syeda Hafsa
command: /sp.specify
labels: ["spec","auth","tickets","worker","email","knowledge-base","onboarding","threading","notifications"]
links:
  spec: specs/004-resolv-completion/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/004-resolv-completion/spec.md
  - specs/004-resolv-completion/checklists/requirements.md
tests:
  - null
---

## Prompt

Resolv is an AI support employee for Shopify brands. The product has three layers:

Backend (hack5/) — FastAPI. Handles tickets, brands, actions, auth, Shopify integration, Gmail per-brand OAuth, WhatsApp webhooks, knowledge base RAG, and AfterShip tracking. ~85% complete.
Admin UI (ai-ops-console/) — React Vite. The brand owner logs in and sees: dashboard stats, ticket list, ticket detail with AI draft approval, pending actions queue (refund/cancel approvals), brand management, settings with knowledge base upload, and onboarding wizard. ~70% complete.
Customer Web Form (web-form/) — React Vite. Customers submit support requests and track ticket status. ~75% complete.

What needs to be completed:
CRITICAL (product cannot function without these):
A. Dual auth bug — v1 login tokens don't authenticate v2 routes. All protected routes must accept v1 JWT tokens. Fix the auth middleware so v1 tokens work on all routes.
B. Dashboard shows real data — currently shows 0 for Active Conversations and 0% AI Handled. Wire dashboard stats to real ticket and action counts from the database.
C. Ticket rows show empty Channel and Sender columns — these fields exist in the database but are not returned or displayed. Fix the ticket list API response and UI to show channel (email/whatsapp) and sender (customer email).
D. Crashing background worker — the email polling worker crashes in a loop. Wrap polling loop in try/except, log errors, sleep 5s on failure, never crash the process.
E. Approve and reply not sending — the approve-ai and respond endpoints exist but emails do not send. Fix Gmail send to actually execute and return clear success/error.
IMPORTANT (product is incomplete without these):
F. Knowledge base UI — the RAG engine works but there is no UI to upload documents. Build the Settings knowledge base tab: upload text with a title, list sources, delete sources.
G. Onboarding wizard — new users see a blank dashboard. Build a 3-step wizard shown when brands.length === 0: step 1 connect Shopify, step 2 connect Gmail, step 3 done.
H. Email threading — customer replies create new tickets instead of adding to existing thread. Use gmail_thread_id to match replies to existing tickets.
I. Browser notifications — show a browser notification when a new ticket arrives or a new action needs approval.

## Response snapshot

Created feature branch `004-resolv-completion` and wrote full specification at
`specs/004-resolv-completion/spec.md`. The spec covers:
- 7 user stories (P1-P3) mapped to items A-I
- 22 functional requirements (FR-001 to FR-022)
- 5 key entities (Ticket, Action, Brand, Knowledge Base Source, Tenant)
- 9 measurable success criteria (SC-001 to SC-009)
- Edge cases for each item
- Assumptions section documenting what is already implemented
- Requirements checklist created and all items pass

No NEEDS CLARIFICATION markers — all items had sufficient context for reasonable defaults.
FR-001/FR-002 (auth fix) identified as the unblocking dependency for all other features.

## Outcome

- ✅ Impact: Full specification ready for planning. Auth fix is P1 unblocking dependency.
- 🧪 Tests: Requirements checklist passes all items
- 📁 Files: specs/004-resolv-completion/spec.md, specs/004-resolv-completion/checklists/requirements.md
- 🔁 Next prompts: /sp.plan to generate architecture and task breakdown
- 🧠 Reflection: All 9 items (A-I) were directly translatable to testable requirements with
  no ambiguity. The dual auth bug (A) must be fixed before any other item can be verified.

## Evaluation notes (flywheel)

- Failure modes observed: none
- Graders run and results (PASS/FAIL): requirements checklist — all PASS
- Prompt variant (if applicable): N/A
- Next experiment: run /sp.plan immediately; auth fix should be task 1
