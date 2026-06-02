---
id: 2
title: Update Constitution Principles
stage: constitution
date: 2026-04-08
surface: agent
model: minimax-m2.5:cloud
feature: none
branch: main
user: default_user
command: /sp.constitution
labels: [constitution, principles, update, e-commerce, ai-saas]
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

/sp.constitution

Create principles for building a production-ready AI SaaS product for e-commerce brands.

Focus on:

1. Code Quality
- Clean, modular, maintainable code
- Strict separation of frontend, backend, and AI logic
- No hardcoded credentials or secrets
- Proper error handling for all API calls (Shopify, email, AI)

2. Multi-Tenant Security
- Every request must include tenant_id
- No cross-tenant data leakage allowed
- All queries filtered by authenticated user
- Encrypt sensitive data (Shopify tokens, email credentials)

3. Reliability & Stability
- System must not crash on failures (Shopify errors, email failures)
- Graceful fallbacks for AI failures
- Retry logic for external APIs where needed

4. AI Behavior Standards
- AI must always check RAG knowledge before generating responses
- AI must NOT auto-execute sensitive actions (refunds, cancellations)
- AI must create structured action proposals for approval

5. User Experience
- Fast, minimal dashboard
- One-click approvals for actions
- Clear status indicators (pending, executed, failed)
- No unnecessary complexity in UI

6. Performance
- Email processing should be near real-time
- Action queue must update instantly
- API responses should be optimized and fast

7. Testing Standards
- Every core flow must be testable:
  - Email → AI → Reply
  - Email → Action → Approval → Shopify execution
- Validate Shopify API responses before execution

8. Product Philosophy
- Focus on "Resolution", not just "Replies"
- Prioritize simplicity over feature bloat
- Build for real e-commerce workflows (refunds, cancellations, tracking)

## Response snapshot

Updated constitution from v1.0.0 to v2.0.0 with all 8 requested principles:
- I. Code Quality - clean, modular, no hardcoded secrets, error handling
- II. Multi-Tenant Security - tenant_id isolation, no cross-tenant leakage, encryption
- III. Reliability & Stability - crash-resistant, graceful fallbacks, retry logic
- IV. AI Behavior Standards - RAG-first, no auto-execute sensitive actions, approval proposals
- V. User Experience - minimal dashboard, one-click approvals, clear status indicators
- VI. Performance - near real-time processing, instant updates, optimized APIs
- VII. Testing Standards - testable core flows, Shopify response validation
- VIII. Product Philosophy - resolution focus, simplicity over features, real e-commerce workflows

Added appropriate Additional Constraints and updated Development Workflow with tenant isolation and Shopify API validation focus.

## Outcome

- ✅ Impact: Updated constitution to v2.0.0 with 8 production-ready AI SaaS principles for e-commerce
- 🧪 Tests: No tests required for constitution update
- 📁 Files: Updated .specify/memory/constitution.md with new principles
- 🔁 Next prompts: Use these principles to guide feature implementation decisions
- 🧠 Reflection: Constitution now aligned with e-commerce AI SaaS product needs (Shopify, multi-tenant, resolution focus)

## Evaluation notes (flywheel)

- Failure modes observed: None
- Graders run and results (PASS/FAIL): N/A
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): Begin feature development following these updated principles