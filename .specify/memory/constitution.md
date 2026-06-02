<!--
Sync Impact Report:
- Version change: 2.1.0 -> 2.2.0
- Project renamed: "AI Customer Success Digital FTE" -> "Resolv"
- Modified principles:
  - IV. AI Behavior Standards: strengthened confidence gating language (MUST NOT → explicit database record requirement)
- Added principles:
  - X. Authentication Integrity (NEW — one auth system, v1 JWT only)
  - XI. Engineering Discipline (NEW — API-first, no mock data, no new packages, error states)
  - XII. Feature Stability (NEW — never break working routes)
- Removed sections: none
- Templates requiring updates: ✅ constitution updated / ⚠ plan-template.md, tasks-template.md pending alignment
- Follow-up TODOs: none
-->

# Resolv Constitution

## Core Principles

### I. Code Quality
All code MUST be clean, modular, and maintainable with strict separation of frontend, backend,
and AI logic. No hardcoded credentials or secrets are permitted — environment variables and
secure configuration management MUST be used for all sensitive data. Every API call to external
services (Shopify, email providers, AI) MUST implement proper error handling with meaningful
error messages and graceful degradation. This ensures the codebase remains extensible,
debuggable, and secure throughout its lifecycle.

### II. Multi-Tenant Security
Every API request and database query MUST include a valid tenant_id for isolation. Cross-tenant
data leakage is strictly prohibited — all queries MUST be filtered by the authenticated user's
tenant context. Sensitive data including Shopify access tokens, email credentials, and API keys
MUST be encrypted at rest and in transit. This protects each brand's customer data from
unauthorized access by other tenants in the multi-tenant system.

### III. Reliability & Stability
The system MUST NOT crash on failures from external services. All Shopify API errors, email
sending failures, and AI timeouts MUST be caught, logged, and handled gracefully with
appropriate user feedback. The system MUST implement retry logic with exponential backoff for
transient failures on external APIs. When AI services fail, the system MUST fall back to simple
rule-based responses or queue the message for human review rather than returning errors to
customers. This ensures continuous operation despite inevitable external service disruptions.

### IV. AI Behavior Standards
The AI agent MUST check the RAG knowledge base before generating any response to ensure
accurate, brand-specific answers. The AI MUST NOT automatically execute sensitive actions
including refunds, order cancellations, or payment processing. Every financial action (refund,
cancel, modify) MUST have an approved action record persisted in the database before any
execution occurs — no exceptions. Confidence gating is sacred: if no approved record exists,
the action MUST NOT run. All AI-generated actions MUST be created as approval requests with
clear summaries visible to a human before execution. This prevents costly mistakes and ensures
human oversight for all high-impact operations.

### V. User Experience
The dashboard MUST be fast and minimal, presenting only essential information for
decision-making. One-click approval buttons MUST be provided for all proposed actions, enabling
rapid response to customer requests. Clear visual status indicators MUST show every item's
state: pending approval, executed successfully, or failed with error details. No unnecessary
complexity or redundant information SHOULD be displayed — each screen MUST focus on the
immediate task at hand.

### VI. Performance
Email processing MUST be near real-time, with incoming messages analyzed and categorized within
seconds. The action queue MUST update instantly upon any state change, using websocket or
polling mechanisms to keep the UI synchronized. API responses MUST be optimized for speed
through appropriate caching, database query optimization, and async processing where applicable.
Users MUST experience sub-second response times for common operations.

### VII. Testing Standards
Every core flow MUST be independently testable: the email-to-AI-to-reply pipeline, and the
email-to-action-to-approval-to-Shopify-execution pipeline. Shopify API responses MUST be
validated against expected schemas before executing any mutations. The test suite MUST include
unit tests for individual components, integration tests for service interactions, and
end-to-end tests that simulate real customer workflows.

### VIII. Frontend Architecture
The UI MUST be built as a unified AI Operations Cockpit for Shopify brands, not as a
traditional SaaS dashboard.

**Event-Driven UI Only**: UI MUST be built around system events, not pages or modules. No
static dashboards or isolated feature pages — the interface MUST respond to real-time system
activity.

**Single Source of Truth**: All frontend data MUST originate from real API endpoints. No
duplicate data sources, no mock/stub data in production code. Every data point flows through
the backend.

**Workflow-First Design**: UI MUST represent the lifecycle of business operations:
Email → AI Decision → Action → Approval → Execution. Each screen MUST visualize a stage in
this workflow, not a feature category.

**Real-Time First**: UI MUST reflect live system state changes instantly. No manual refresh
flows — the interface MUST auto-update to show new tickets, pending actions, and status changes
as they happen.

**Error States Required**: Every API call in the UI MUST explicitly handle three states:
loading, error, and empty. Unhandled promise rejections and silent failures are not acceptable.

### IX. Product Philosophy
The system MUST focus on "Resolution" as the primary success metric — not just sending replies.
Every interaction SHOULD aim to fully resolve the customer's issue. Simplicity MUST be
prioritized over feature bloat — every new feature MUST justify its complexity cost. The system
MUST be built for real e-commerce workflows including refund processing, order cancellation,
shipment tracking, and inventory inquiries. Features that do not directly support these core
workflows SHOULD NOT be implemented.

**No SaaS Dashboard Patterns**: The UI MUST NOT resemble traditional admin panels with CRUD
tables or tab-heavy layouts. The system MUST feel like a real-time operations control center,
not a configuration screen.

### X. Authentication Integrity
The system MUST use exactly one authentication mechanism: v1 JWT tokens issued by
`/api/v1/auth/login`, sent as `Authorization: Bearer <token>` on every protected request. The
v2 Supabase Auth system MUST NOT be used for any route consumed by the admin console or
web-form. Mixing v1 and v2 auth in a single user flow is strictly prohibited. Any route added
to the backend MUST declare its auth dependency explicitly and MUST be consistent with the
tenant-auth middleware (`get_current_tenant`). This rule exists to prevent the class of bug
where login succeeds but subsequent requests silently fail due to token incompatibility.

### XI. Engineering Discipline
**API-First**: Every UI feature MUST have a working, tested backend endpoint before any
frontend code is written. Building UI against an unimplemented or mocked endpoint is not
permitted.

**No Mock Data in Production**: All UI components MUST fetch from real API endpoints. Hardcoded
arrays, stub responses, and placeholder data are only acceptable in isolated test files, never
in production component code.

**Dependency Minimalism**: New packages MUST NOT be installed unless absolutely necessary and
explicitly justified. All solutions MUST first be attempted using packages already present in
`requirements.txt` (backend) and `package.json` (frontend). This prevents dependency bloat,
security surface expansion, and build instability.

**Explicit Error States**: Every API call in the UI MUST handle loading, error, and empty
states. Components that render nothing on error, or that silently swallow exceptions, violate
this principle.

### XII. Feature Stability
Working features MUST NOT be broken. If a route, component, or integration is confirmed
working, it MUST NOT be modified unless the current task explicitly requires changing it.
Refactoring, cleanup, and "while I'm here" changes to working code are prohibited during
feature development. All changes MUST be scoped to exactly what the task requires — no
more, no less.

## Additional Constraints

**Technology Stack**: Python FastAPI (backend), React + Vite (ai-ops-console admin UI),
React (web-form customer UI), PostgreSQL via Supabase, pgvector for knowledge base. Shopify
Admin API for e-commerce operations, SMTP/IMAP for customer email.

**Security Requirements**: Tenant isolation MUST be enforced at both application and database
layers. All API keys, tokens, and credentials MUST be stored encrypted and accessed only
through environment variables. Regular security audits MUST be performed for tenant isolation
bypass attempts and credential exposure risks.

**Performance Standards**: Email processing latency under 5 seconds for 95% of messages,
action queue updates within 500ms, API response times under 1 second for read operations,
and 99.9% uptime SLA.

## Development Workflow

**Code Review Process**: All changes require peer review with specific focus on tenant
isolation verification, auth system consistency (v1 only), Shopify API response validation,
error handling completeness, and security implication analysis. At least one reviewer MUST
verify compliance with all core principles before merge.

**Quality Gates**: Automated tests MUST achieve 80%+ code coverage on core business logic,
security scanning MUST pass with no high-severity findings, and performance benchmarks MUST
be met for all critical user paths before deployment.

**Deployment Policy**: Progressive rollouts with feature flags for gradual user exposure,
rollback capabilities within 5 minutes of detecting issues, and continuous monitoring of
resolution rates and customer satisfaction metrics post-deployment.

## Governance

This constitution supersedes all other development practices and guidelines. All code reviews,
architectural decisions, and system modifications MUST be evaluated against these principles.
Any proposed changes to these core principles require formal amendment procedures with
stakeholder approval and comprehensive impact analysis.

All pull requests and code reviews MUST verify compliance with each principle. System
complexity MUST be justified by clear customer value and resolution improvement. Use this
document as the primary guidance for all development decisions.

**Version**: 2.2.0 | **Ratified**: 2026-02-03 | **Last Amended**: 2026-05-13
