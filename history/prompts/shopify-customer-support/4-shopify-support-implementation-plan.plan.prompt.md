---
id: 4
title: Shopify Support Implementation Plan
stage: plan
date: 2026-04-08
surface: agent
model: minimax-m2.5:cloud
feature: shopify-customer-support
branch: main
user: default_user
command: /sp.plan
labels: [plan, shopify, ai-support, multi-tenant, architecture]
links:
  spec: specs/002-shopify-customer-support/spec.md
  ticket: null
  adr: null
  pr: null
files:
 - specs/002-shopify-customer-support/plan.md
 - specs/002-shopify-customer-support/research.md
 - specs/002-shopify-customer-support/data-model.md
 - specs/002-shopify-customer-support/quickstart.md
 - specs/002-shopify-customer-support/contracts/openapi.yaml
tests:
 - none
---

## Prompt

Build a full-stack SaaS system with the following architecture:

Frontend:
- React (existing dashboard in E:/ai-ops-console)
- Minimal UI with focus on speed and clarity
- Pages: Inbox (tickets), Action Queue, History, Settings

Backend:
- Python (existing hack5 backend)
- REST API structure
- Dockerized environment

Authentication:
- Supabase Auth
- JWT-based authentication
- Attach tenant_id to every request

Database:
- PostgreSQL (Supabase or local)
- Tables: users, tenants, tickets, actions, logs, knowledge_base

Email Integration:
- Phase 1: Email forwarding (IMAP/SMTP or webhook ingestion)
- Phase 2: OAuth (Gmail/Outlook)

AI Layer:
- LLM for intent detection and response generation
- RAG pipeline with per-tenant embeddings

Shopify Integration:
- Admin API via access token
- Actions: refund, cancel order, update address

Core Backend Modules:
- email_processor.py, ai_engine.py, action_service.py, shopify_service.py, auth_middleware.py

Error Handling:
- Handle Shopify API errors, validate order existence, retry or log failures

Security:
- Encrypt tokens, strict tenant isolation

## Response snapshot

Created complete implementation plan with:
- plan.md: Technical context, constitution check (all 8 principles PASS), project structure
- research.md: 4 research tasks resolved (Shopify API, Supabase multi-tenant, Mistral AI, Email handling)
- data-model.md: 7 entities with full schema, indexes, RLS policies, state transitions
- contracts/openapi.yaml: Full OpenAPI 3.0 spec for 11 API endpoints
- quickstart.md: Setup guide with Docker, environment, usage flow

Key decisions:
- Shopify REST API with async httpx (not GraphQL)
- RLS with tenant_id for multi-tenant isolation
- Mistral AI via OpenAI-compatible API for intent detection
- IMAP polling for email ingestion (30s interval)
- pgvector on Supabase for RAG embeddings

## Outcome

- ✅ Impact: Created comprehensive implementation architecture for Shopify Customer Support AI
- 🧪 Tests: No tests required for plan creation
- 📁 Files: Created 5 artifacts in specs/002-shopify-customer-support/
- 🔁 Next prompts: Run /sp.tasks to generate implementation tasks
- 🧠 Reflection: Plan leverages existing codebase infrastructure (shopify_service, rag_engine, email_poller already exist)

## Evaluation notes (flywheel)

- Failure modes observed: None
- Graders run and results (PASS/FAIL): N/A
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): Generate tasks with /sp.tasks