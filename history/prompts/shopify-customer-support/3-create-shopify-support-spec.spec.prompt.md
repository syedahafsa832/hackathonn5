---
id: 3
title: Create Shopify Support Spec
stage: spec
date: 2026-04-08
surface: agent
model: minimax-m2.5:cloud
feature: shopify-customer-support
branch: main
user: default_user
command: /sp.specify
labels: [spec, shopify, ai-support, multi-tenant]
links:
  spec: specs/002-shopify-customer-support/spec.md
  ticket: null
  adr: null
  pr: null
files:
 - specs/002-shopify-customer-support/spec.md
tests:
 - none
---

## Prompt

Build a multi-tenant AI-powered customer support system for Shopify e-commerce brands.

The system focuses on handling customer emails and webform requests, automatically responding to simple queries and preparing operational actions (refunds, cancellations, address changes) for human approval.

Core goals:
- Replace traditional support teams with an AI system that resolves customer issues
- Minimize human involvement to only approving sensitive actions
- Provide fast, reliable, and scalable support for e-commerce brands

Core Features:
1. Email & Webform Handling - System receives incoming customer emails via forwarding or API. Webform submissions treated like emails. Each message becomes a ticket.
2. AI Processing Engine - Detect intent (refund, cancel, question, etc.), analyze sentiment and urgency, retrieve relevant information from brand-specific knowledge base (RAG), generate response draft.
3. Decision Layer - If request is simple → auto reply. If request involves money or risk → create action proposal.
4. Action System - Actions include refunds, order cancellations, address changes. Each action includes order ID, suggested operation, confidence score, risk level.
5. Human-in-the-Loop Approval - Actions appear in Action Queue. User can approve or reject in one click. Upon approval, action executes in Shopify.
6. Shopify Integration - Each tenant connects their Shopify store. System can read orders and execute actions via API.
7. Dashboard (Minimal) - Inbox (tickets), Action Queue (main feature), History (logs of actions and replies), Settings (connect Shopify + email).
8. Multi-Tenant System - Each brand has isolated data. Users only see their own tickets, actions, and settings.
9. Logging & Transparency - Track all events: email received, AI replied, action created, approved/rejected, executed/failed.

Goal Outcome: Customer sends email → AI processes → resolves automatically OR prepares action → user approves → system executes → customer notified.

Clarifying answers:
- Auth: Supabase Auth
- Email Ingest: Email forwarding (catch-all with polling)
- AI Provider: Mistral AI
- Reply Delivery: SMTP sending

## Response snapshot

Created comprehensive spec at specs/002-shopify-customer-support/spec.md with:
- 12 prioritized user stories (P1-P3)
- 31 functional requirements (FR-001 to FR-031)
- Key entities: Tenant, Ticket, Action, Customer, Order, KnowledgeBase, AuditLog
- 8 measurable success criteria
- Technology choices documented: Supabase Auth, email forwarding, Mistral AI, SMTP

## Outcome

- ✅ Impact: Created full feature specification for Shopify Customer Support AI
- 🧪 Tests: No tests required for spec creation
- 📁 Files: Created specs/002-shopify-customer-support/spec.md
- 🔁 Next prompts: Run /sp.plan to create implementation architecture
- 🧠 Reflection: Spec covers all 9 core features from user input with P1/P2/P3 prioritization

## Evaluation notes (flywheel)

- Failure modes observed: None
- Graders run and results (PASS/FAIL): N/A
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): Proceed to planning phase with /sp.plan