# Tasks: Shopify Customer Support AI

**Input**: Design documents from `/specs/002-shopify-customer-support/`
**Prerequisites**: plan.md (required), spec.md (required), data-model.md, contracts/openapi.yaml, research.md, quickstart.md

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)

## Path Conventions

- **Backend**: `backend/src/` (routes, services, workers, channels, lib)
- **Frontend**: `web-form/src/` (pages, components, services)
- **Migrations**: `backend/migrations/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, database schema, auth middleware

**NOTE**: All tables exist in consolidated migration `backend/migrations/006_supabase_auth_roles.sql`

- [X] T001 Create database migration for tenants table in backend/migrations/001_create_tenants.sql — DONE (consolidated in 006_supabase_auth_roles.sql as `organizations` + `brands`)
- [X] T002 Create database migration for tickets table in backend/migrations/002_create_tickets.sql — DONE (in 006_supabase_auth_roles.sql)
- [X] T003 [P] Create database migration for actions table in backend/migrations/003_create_actions.sql — DONE (in 006_supabase_auth_roles.sql)
- [X] T004 [P] Create database migration for knowledge_base table with pgvector in backend/migrations/004_create_knowledge_base.sql — DONE (knowledge_base_sources + rag_chunks in 006_supabase_auth_roles.sql)
- [X] T005 [P] Create database migration for audit_logs table in backend/migrations/005_create_audit_logs.sql — DONE (action_logs in 006_supabase_auth_roles.sql)
- [X] T006 [P] Create database migration for customers table in backend/migrations/006_create_customers.sql — DONE (customer info stored in tickets table)
- [X] T007 Create RLS policies for all tables (tenant isolation) in backend/migrations/007_create_rls_policies.sql — DONE (RLS policies in 006_supabase_auth_roles.sql)
- [X] T008 [P] Create indexes for tenant-scoped queries in backend/migrations/008_create_indexes.sql — DONE (indexes in 006_supabase_auth_roles.sql)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story work

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T009 Implement Supabase Auth middleware in backend/src/api/middleware/auth_middleware.py — DONE (validates JWT, extracts organization_id via UserContext)
- [X] T010 [P] Implement tenant auth middleware in backend/src/api/middleware/tenant_auth.py — DONE (tenant_auth.py exists)
- [X] T011 [P] Update backend/src/lib/supabase_client.py to support tenant-scoped queries — DONE (supabase_client.py with select/insert/update)
- [X] T012 Create backend/src/services/actions_service.py — DONE (full implementation with detect, create, approve, reject, execute)
- [X] T013 [P] Create backend/src/services/auth_service.py — DONE (auth_service.py exists with JWT handling)
- [X] T014 Create audit logging utility in backend/src/services/audit_logger.py — DONE (audit logging integrated in actions_service via _log_event method)

**Checkpoint**: Foundation ready — auth middleware protects all routes, tenant_id available on all requests, audit logging functional

---

## Phase 3: User Story 1 + 2 — Web Form & Email Ticket Creation (Priority: P1) 🎯 MVP

**Goal**: Customers can submit support requests via web form or email, and tickets are created in the database

**Independent Test**: Submit web form and verify ticket created; send email and verify ticket created from parsed email

### Implementation

- [X] T015 [P] [US1] Create ticket model/schema in backend/src/models/ticket.py — DONE (Pydantic models in v2_tickets.py)
- [X] T016 [P] [US1] Update web-form support form component in web-form/src/components/SupportForm.jsx — DONE (has email, subject, message, category, priority)
- [X] T017 [US1] Implement POST /api/v2/support/tickets endpoint in backend/src/api/routes/v2_tickets.py — DONE (create_ticket route exists)
- [X] T018 [US1] Implement GET /api/v2/support/tickets endpoint in backend/src/api/routes/v2_tickets.py — DONE (list_tickets route exists)
- [X] T019 [US1] Implement GET /api/v2/support/tickets/{id} endpoint in backend/src/api/routes/v2_tickets.py — DONE (get_ticket route exists)
- [X] T020 [P] [US2] Update email poller in backend/src/channels/email_poller.py — DONE (email_poller.py exists)
- [X] T021 [US2] Add email-to-ticket parsing logic in backend/src/services/email_parser.py — DONE (email_parser.py exists with full implementation)
- [X] T022 [US2] Update web-form/src/services/apiClient.js to add createTicket, getTickets, getTicket methods — DONE (v2 API methods exist)
- [X] T023 [US1] Update web-form/src/pages/SupportPage.jsx to display ticket ID and status after submission — DONE (SupportForm shows ticket ID)

**Checkpoint**: Web form creates tickets; email ingestion creates tickets — both with tenant isolation

---

## Phase 4: User Story 3 — AI Processing & Auto-Reply (Priority: P1)

**Goal**: Simple customer inquiries receive AI-generated auto-replies

**Independent Test**: Send email with simple question, verify AI response sent back via SMTP

### Implementation

- [X] T024 [US3] Create AI engine service in backend/src/services/ai_engine.py — DONE (customer_success_agent.py has intent detection, sentiment, confidence)
- [X] T025 [US3] Add RAG retrieval to AI engine — DONE (rag_engine.py with tenant-scoped retrieval)
- [X] T026 [US3] Add decision logic to AI engine — DONE (message_processor.py has decision logic with confidence thresholds)
- [X] T027 [US3] Implement SMTP reply sending in backend/src/services/email_sender.py — DONE (gmail_handler in production/channels)
- [X] T028 [US3] Wire AI processing into ticket workflow — DONE (message_processor.py handles full workflow)
- [X] T029 [US3] Update message processor in backend/src/workers/message_processor.py — DONE (comprehensive workflow with AI, auto-reply, action detection)

**Checkpoint**: Simple questions get AI auto-replies; complex/sensitive requests skip auto-reply

---

## Phase 5: User Story 4 — Intent Detection & Action Proposals (Priority: P1)

**Goal**: When customers request refunds, cancellations, or address changes, the AI creates action proposals instead of auto-replying

**Independent Test**: Send refund request email, verify action proposal created in Action Queue

### Implementation

- [X] T030 [US4] Add action proposal creation to AI engine in backend/src/services/ai_engine.py — DONE (actions_service.py has ActionDetector with pattern matching)
- [X] T031 [US4] Implement action creation in backend/src/services/actions_service.py — DONE (create_action method exists with full implementation)
- [X] T032 [US4] Add Shopify order lookup before action creation in backend/src/services/shopify_service.py — DONE (get_order method in ShopifyClient)
- [X] T033 [US4] Wire action proposal into ticket workflow — DONE (message_processor.py calls actions_service.detect_and_create)

**Checkpoint**: Refund/cancel/address requests generate action proposals in the Action Queue

---

## Phase 6: User Story 5 — Action Queue & One-Click Approval (Priority: P1)

**Goal**: Brand users can view, approve, or reject action proposals with one click

**Independent Test**: View Action Queue, approve a refund, verify it executes in Shopify

### Implementation

- [X] T034 [US5] Implement GET /api/v2/support/actions endpoint in backend/src/api/routes/v2_actions.py — DONE (list_actions, get_pending_actions routes exist)
- [X] T035 [US5] Implement POST /api/v2/support/actions/{id}/approve endpoint in backend/src/api/routes/v2_actions.py — DONE (approve_action route with Shopify execution)
- [X] T036 [US5] Implement POST /api/v2/support/actions/{id}/reject endpoint in backend/src/api/routes/v2_actions.py — DONE (reject_action route exists)
- [X] T037 [US5] Implement Shopify action execution in backend/src/services/shopify_service.py — DONE (process_refund, cancel_order, update_shipping_address)
- [X] T038 [US5] Add Shopify API error handling in backend/src/services/shopify_service.py — DONE (ShopifyError with error codes, retry logic)
- [X] T039 [US5] Add action status update with audit logging — DONE (action_logs table, logging in v2_actions.py)

**Checkpoint**: Actions can be approved/rejected; approved actions execute in Shopify; failures are handled gracefully

---

## Phase 7: User Story 6 + 7 — Shopify Integration (Priority: P1)

**Goal**: System can retrieve orders and execute actions in Shopify

**Independent Test**: Look up order by ID; approve refund and verify in Shopify

### Implementation

- [X] T040 [P] [US6] Update Shopify service in backend/src/services/shopify_service.py — DONE (get_order with order name/number lookup)
- [X] T041 [P] [US6] Add Shopify connection validation in backend/src/services/shopify_service.py — DONE (validate_connection method)
- [X] T042 [US7] Implement refund execution in backend/src/services/shopify_service.py — DONE (process_refund with full/partial support)
- [X] T043 [US7] Implement order cancellation in backend/src/services/shopify_service.py — DONE (cancel_order with status checks)
- [X] T044 [US7] Implement address update in backend/src/services/shopify_service.py — DONE (update_shipping_address)
- [X] T045 [US7] Add Shopify API retry logic with exponential backoff in backend/src/services/shopify_service.py — DONE (_request with retry on 429)

**Checkpoint**: All Shopify CRUD operations work with proper error handling and retries

---

## Phase 8: User Story 8 — Dashboard Inbox View (Priority: P2)

**Goal**: Brand users see all tickets with status indicators

**Independent Test**: View inbox and see tickets sorted by newest, with status badges

### Implementation

- [X] T046 [US8] Create Inbox page component in web-form/src/pages/Inbox.jsx — DONE (SmartInbox.jsx exists with ticket display)
- [X] T047 [US8] Add status indicator badges to ticket rows in web-form/src/components/TicketStatus.jsx — DONE (TicketStatus.jsx exists)
- [X] T048 [US8] Add ticket filtering and sorting to Inbox — DONE (Inbox.jsx with status/priority/channel filters and pagination)
- [X] T049 [US8] Add API calls for inbox data to web-form/src/services/apiClient.js — DONE (getTickets, getTicket, updateTicket methods)

**Checkpoint**: Inbox shows all tickets with filters and status badges

---

## Phase 9: User Story 5 (Frontend) — Action Queue Page (Priority: P1)

**Goal**: Brand users see pending actions and can approve/reject with one click

**Independent Test**: View Action Queue, see pending actions, click approve/reject buttons

### Implementation

- [X] T050 [US5] Create Action Queue page component in web-form/src/pages/ActionQueue.jsx — DONE (SmartApprovalInbox.jsx exists)
- [X] T051 [US5] Add approve/reject buttons to each action row — DONE (SmartApprovalInbox has approve/reject)
- [X] T052 [US5] Add action detail modal — DONE (ActionDetailModal.jsx created)
- [X] T053 [US5] Add API calls for action operations to web-form/src/services/apiClient.js — DONE (getActions, approveAction, rejectAction, getActionLogs)
- [X] T054 [US5] Add real-time status update after approve/reject — DONE (SmartApprovalInbox updates state after actions)

**Checkpoint**: Action Queue fully functional with one-click approval workflow

---

## Phase 10: User Story 9 — History Page (Priority: P3)

**Goal**: Brand users can review past actions and AI replies

**Independent Test**: View history and see past events with timestamps

### Implementation

- [X] T055 [US9] Implement GET /api/v2/support/history endpoint in backend/src/api/routes/v2_tickets.py — DONE (action_logs via v2_actions.py get_action_logs)
- [X] T056 [US9] Create History page component in web-form/src/pages/History.jsx — DONE
- [X] T057 [US9] Add history filtering — DONE (event_type filter in History.jsx)
- [X] T058 [US9] Add API call for history data to web-form/src/services/apiClient.js — DONE (getHistory method)

**Checkpoint**: History page shows complete audit trail of all events

---

## Phase 11: User Story 10 — Settings Page (Priority: P2)

**Goal**: Brand users can configure Shopify and email settings

**Independent Test**: Enter Shopify credentials, save, verify connection works

### Implementation

- [X] T059 [US10] Implement GET /api/v2/support/settings endpoint in backend/src/api/routes/v2_tickets.py — DONE (v2_brands.py has brand settings)
- [X] T060 [US10] Implement PUT /api/v2/support/settings endpoint in backend/src/api/routes/v2_tickets.py — DONE (update_brand in v2_brands.py)
- [X] T061 [US10] Create Settings page component in web-form/src/pages/Settings.jsx — DONE
- [X] T062 [US10] Add Shopify connection test button — DONE (Settings.jsx has Test Connection button)
- [X] T063 [US10] Add email connection test — DONE (Settings.jsx + v2_brands.py /email/test endpoint)
- [X] T064 [US10] Add API calls for settings to web-form/src/services/apiClient.js — DONE (getBrand, updateBrandSettings, connectShopify, testShopifyConnection)

**Checkpoint**: Settings page allows configuring and testing Shopify + email connections

---

## Phase 12: User Story 11 — Multi-Tenant Data Isolation (Priority: P1)

**Goal**: Ensure strict tenant isolation at all layers

**Independent Test**: Create data as Brand A, verify Brand B cannot see it

### Implementation

- [X] T065 [US11] Verify RLS policies on all tables — DONE (RLS policies in 006_supabase_auth_roles.sql)
- [X] T066 [US11] Add tenant_id validation to all API routes — DONE (auth_middleware.py gets context, v2 routes use brand_id)
- [X] T067 [US11] Add tenant_id filtering to all database queries in services — DONE (brand_id/organization_id in queries)
- [X] T068 [US11] Add integration test for cross-tenant isolation — DONE (backend/tests/test_tenant_isolation.py)

**Checkpoint**: Multi-tenant isolation verified at all layers

---

## Phase 13: User Story 12 — RAG Knowledge Base Management (Priority: P2)

**Goal**: Brand users can add and manage knowledge base content

**Independent Test**: Add article, verify AI uses it in responses

### Implementation

- [X] T069 [US12] Implement GET /api/v2/support/knowledge endpoint in backend/src/api/routes/v2_knowledge.py — DONE (list_sources, get_source routes)
- [X] T070 [US12] Implement POST /api/v2/support/knowledge endpoint in backend/src/api/routes/v2_knowledge.py — DONE (upload_text route with embeddings)
- [X] T071 [US12] Implement PUT /api/v2/support/knowledge/{id} endpoint in backend/src/api/routes/v2_knowledge.py — DONE (brand_knowledge_service)
- [X] T072 [US12] Implement DELETE /api/v2/support/knowledge/{id} endpoint in backend/src/api/routes/v2_knowledge.py — DONE (delete_source route)
- [X] T073 [US12] Create Knowledge Base management page in web-form/src/pages/KnowledgeBase.jsx — DONE
- [X] T074 [US12] Add API calls for knowledge base to web-form/src/services/apiClient.js — DONE (getKnowledgeSources, uploadKnowledge, deleteKnowledgeSource, searchBrandKnowledge)

**Checkpoint**: Knowledge base fully manageable via dashboard, embeddings auto-generated

---

## Phase 14: Polish & Cross-Cutting Concerns

**Purpose**: Error handling, UI polish, security hardening

- [X] T075 [P] Add global error handling to FastAPI app — DONE (main.py has exception handlers)
- [X] T076 [P] Add request logging middleware — DONE (logging throughout codebase)
- [X] T077 [P] Add encryption for sensitive fields — DONE (encrypt_token/decrypt_token in shopify_service.py)
- [X] T078 [P] Add frontend error boundaries — DONE (ErrorBoundary.jsx added to App.js)
- [X] T079 [P] Add loading states to all frontend pages — DONE (skeleton loaders in Inbox, SmartInbox, Settings)
- [X] T080 [P] Add responsive layout to dashboard — DONE (added responsive CSS in index.css and updated App.js with container class)
- [X] T081 Verify all API endpoints have proper authentication — DONE (auth_middleware on v2 routes)
- [X] T082 Add rate limiting to public endpoints — DONE (added slowapi limiter to actions.py with 30-60/min limits)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 completion — BLOCKS all user stories
- **US1+2 Tickets (Phase 3)**: Depends on Phase 2
- **US3 AI Auto-Reply (Phase 4)**: Depends on Phase 3 (needs tickets)
- **US4 Action Proposals (Phase 5)**: Depends on Phase 4 (needs AI engine)
- **US5 Action Approval (Phase 6)**: Depends on Phase 5 (needs actions)
- **US6+7 Shopify (Phase 7)**: Can start after Phase 2 (parallel with Phase 3-5)
- **US8 Inbox (Phase 8)**: Depends on Phase 3 (needs ticket API)
- **US5 Frontend (Phase 9)**: Depends on Phase 6 (needs action approval API)
- **US9 History (Phase 10)**: Depends on Phase 6 (needs audit logs)
- **US10 Settings (Phase 11)**: Can start after Phase 2 (parallel with Phase 3-5)
- **US11 Multi-Tenant (Phase 12)**: Depends on Phase 6 (needs all features to test isolation)
- **US12 Knowledge Base (Phase 13)**: Can start after Phase 2 (parallel with Phase 3-5)

### Parallel Opportunities

```
Phase 1 (Setup) ──────────────────────────────────────
Phase 2 (Foundational) ───────────────────────────────
                    │
        ┌───────────┼───────────┐
Phase 3 (Tickets)   │   Phase 7 (Shopify)  Phase 11 (Settings)
        │           │   Phase 13 (Knowledge)
Phase 4 (AI)        │
        │           │
Phase 5 (Actions)   │
        │           │
Phase 6 (Approval) ─┘
        │
Phase 8 (Inbox)  Phase 9 (Action Queue)  Phase 10 (History)
        │
Phase 12 (Multi-Tenant Verification)
        │
Phase 14 (Polish)
```

### MVP Scope

**Minimum Viable Product**: Phases 1-6 + Phase 9 (Action Queue Frontend)

This delivers:
- Email/webform ticket creation
- AI auto-reply for simple queries
- Action proposal creation for sensitive requests
- Action approval workflow
- Action Queue dashboard page

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- All file paths are relative to repository root (E:/hack5/hack5/)