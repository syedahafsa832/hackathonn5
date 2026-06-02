# Implementation Plan: Shopify Customer Support AI

**Branch**: `002-shopify-customer-support` | **Date**: 2026-04-08 | **Spec**: `specs/002-shopify-customer-support/spec.md`

**Input**: Feature specification for multi-tenant AI-powered customer support system for Shopify e-commerce brands. Focus on handling customer emails and webform requests, AI processing, action proposals, and Shopify integration.

## Summary

Build a multi-tenant AI-powered customer support system for Shopify e-commerce brands that handles customer emails and webform submissions. The system automatically responds to simple queries using Mistral AI with RAG knowledge base retrieval, and creates operational action proposals (refunds, cancellations, address changes) for human approval before executing in Shopify. Uses Supabase Auth for authentication, PostgreSQL with pgvector for data and embeddings, and implements strict tenant isolation.

## Technical Context

**Language/Version**: Python 3.11+, JavaScript (React)
**Primary Dependencies**: FastAPI, React/Next.js, Supabase (PostgreSQL + pgvector), Mistral AI, Shopify Admin API
**Storage**: PostgreSQL via Supabase, with pgvector for RAG embeddings
**Testing**: pytest for backend, Jest/React Testing Library for frontend
**Target Platform**: Linux server (Docker), Web browser
**Project Type**: web (backend + frontend SaaS)
**Performance Goals**: Email processing < 5s, action queue updates < 500ms, API responses < 1s
**Constraints**: Multi-tenant isolation, strict tenant_id enforcement on all requests
**Scale/Scope**: Multiple brands (tenants), each with customers, tickets, actions, knowledge base

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Based on constitution v2.0.0 principles:

| Principle | Requirement | Status |
|-----------|-------------|--------|
| I. Code Quality | Clean modular code, no hardcoded secrets, error handling | ✅ Will implement |
| II. Multi-Tenant Security | tenant_id on every request, no cross-tenant leakage | ✅ Will implement |
| III. Reliability & Stability | No crashes on failures, retry logic, graceful fallbacks | ✅ Will implement |
| IV. AI Behavior Standards | RAG-first, no auto-execute sensitive actions, approval required | ✅ Will implement |
| V. User Experience | Minimal dashboard, one-click approvals, clear status | ✅ Will implement |
| VI. Performance | Near real-time email processing, instant action queue updates | ✅ Will implement |
| VII. Testing Standards | Testable core flows, Shopify response validation | ✅ Will implement |
| VIII. Product Philosophy | Resolution focus, simplicity, e-commerce workflows | ✅ Will implement |

**Gate Status**: PASS - All constitutional requirements will be addressed in implementation

## Project Structure

### Documentation (this feature)

```text
specs/002-shopify-customer-support/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md            # Phase 2 output (/sp.tasks)
```

### Source Code (repository root)

The project reuses existing infrastructure:

```text
E:/hack5/hack5/
├── backend/                     # Python FastAPI backend
│   ├── src/
│   │   ├── api/routes/         # API endpoints (v2_tickets, v2_actions, etc.)
│   │   ├── services/           # Business logic (shopify_service, rag_engine, etc.)
│   │   ├── channels/           # Email polling
│   │   ├── workers/            # Background workers
│   │   └── lib/                # Utilities
│   └── main.py                 # FastAPI entry point
│
├── web-form/                   # React frontend
│   ├── src/
│   │   ├── pages/              # Page components (Inbox, ActionQueue, History, Settings)
│   │   ├── components/         # Reusable components
│   │   └── services/           # API client
│   └── package.json
│
└── docker-compose.yml          # All services orchestration
```

**Structure Decision**: Reuse existing backend (`backend/`) and frontend (`web-form/`) structures. Add new routes and services as needed for the Shopify Customer Support feature. New modules:
- `src/services/action_service.py` - Create and execute actions
- `src/api/routes/support.py` - Support ticket endpoints (existing)
- Extend existing `rag_engine.py`, `shopify_service.py`, `email_poller.py`

## Phase 0: Research

### Research Tasks

| Task | Topic | Status |
|------|-------|--------|
| R1 | Shopify Admin API best practices for refunds, cancellations, address updates | COMPLETE |
| R2 | Supabase multi-tenant patterns with pgvector | COMPLETE |
| R3 | Mistral AI integration patterns for intent detection | COMPLETE |
| R4 | Email IMAP/SMTP handling for incoming ticket processing | COMPLETE |

### Research Findings

**R1: Shopify Admin API**
- Use `shopify_api` Python library or direct REST calls
- Refunds: POST `/admin/api/2024-01/orders/{order_id}/refunds.json`
- Cancel: POST `/admin/api/2024-01/orders/{order_id}/cancel.json`
- Address update: PUT `/admin/api/2024-01/orders/{order_id}/shipping_address.json`
- Handle rate limiting with exponential backoff

**R2: Supabase Multi-Tenant**
- Use RLS (Row Level Security) policies for tenant isolation
- Include `tenant_id` in all queries via Supabase client
- Use Supabase Auth with custom claims for tenant_id
- Store encrypted tokens in a secure table per tenant

**R3: Mistral AI Integration**
- Use OpenAI-compatible API endpoint
- System prompt for intent detection: categorize as refund_request, cancel_request, address_change_request, question, other
- Include confidence score in response
- RAG: retrieve relevant knowledge base content before generating response

**R4: Email Handling**
- IMAP polling via `imaplib` or `aiomailbox` for async
- SMTP via `aiosmtplib` for sending
- Parse email to extract: from email, subject, body, order numbers

## Phase 1: Design & Contracts

### Entities (from spec)

| Entity | Description |
|--------|-------------|
| Tenant | Brand account in multi-tenant system |
| Ticket | Customer support request from email or web form |
| Action | Proposed operational action (refund, cancel, address_change) |
| Customer | Customer info from emails |
| Order | Shopify order data |
| KnowledgeBase | Brand-specific RAG content |
| AuditLog | Event tracking |

### API Contracts

See `contracts/` directory for full OpenAPI specifications.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v2/support/tickets` | POST | Create ticket from email/webform |
| `/api/v2/support/tickets` | GET | List tenant's tickets |
| `/api/v2/support/tickets/{id}` | GET | Get ticket details |
| `/api/v2/support/actions` | GET | List pending actions |
| `/api/v2/support/actions/{id}/approve` | POST | Approve action |
| `/api/v2/support/actions/{id}/reject` | POST | Reject action |
| `/api/v2/support/knowledge` | GET/POST | Manage knowledge base |
| `/api/v2/support/settings` | GET/PUT | Tenant settings |
| `/api/v2/support/history` | GET | Audit log history |

## Complexity Tracking

| Complexity | Why Needed | Simpler Alternative Rejected Because |
|------------|------------|--------------------------------------|
| Multi-tenant isolation | Core SaaS requirement | Single-tenant doesn't support multiple brands |
| RAG knowledge base | Brand-specific AI responses | Generic responses don't work for e-commerce |
| Shopify integration | Core value - execute actions | Manual action execution defeats automation |
| Action approval workflow | Human oversight for sensitive ops | Auto-execution risks costly mistakes |

---

*Plan created by /sp.plan command. Next step: /sp.tasks to generate implementation tasks.*