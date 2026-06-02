# Feature Specification: Resolv MVP Completion

**Feature Branch**: `004-resolv-completion`
**Created**: 2026-05-13
**Status**: Draft

## Overview

Resolv is an AI support employee for Shopify brands. This specification covers the remaining
work to make the product fully functional: five critical fixes that prevent the product from
working at all, and four important completions that make the product usable end-to-end.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Brand Owner Logs In and Sees Real Data (Priority: P1)

A brand owner navigates to the admin console, enters their email and password, and is taken to
a dashboard showing their actual ticket count, active conversations, and AI handled percentage.
They can browse the ticket list and see each ticket's channel (email or WhatsApp) and the
customer's email address in the sender column.

**Why this priority**: Login is the entry point to the entire product. If auth is broken or
the dashboard shows no data, the product appears non-functional to every user.

**Independent Test**: Create a tenant account, submit a test support ticket via the web form,
log in to the admin console, verify the dashboard shows at least 1 active ticket, and verify
the ticket list shows channel and sender fields populated.

**Acceptance Scenarios**:

1. **Given** a registered brand owner, **When** they log in with valid credentials, **Then**
   they land on the dashboard without being redirected back to login.
2. **Given** at least one ticket exists in the database, **When** the dashboard loads, **Then**
   Active Conversations shows the correct count and AI Handled % reflects actual data.
3. **Given** a ticket submitted via email, **When** the ticket list renders, **Then** the
   Channel column shows "email" and the Sender column shows the customer's email address.
4. **Given** a brand owner's auth token, **When** any protected API route is called, **Then**
   the request succeeds and does not return 401.

---

### User Story 2 — Brand Owner Approves an AI Reply and Customer Receives It (Priority: P1)

A brand owner opens a ticket, reads the AI-generated draft response, clicks "Approve & Send",
and the customer receives the email. The ticket status updates to resolved. If Gmail is not
connected, the brand owner sees a clear error explaining what to do next.

**Why this priority**: The core value proposition of Resolv is AI-drafted replies sent on the
brand's behalf. If email sending does not work, the product delivers no value.

**Independent Test**: Submit a test ticket, trigger AI response generation, approve the draft
from the admin console, and verify the email arrives in the test customer inbox.

**Acceptance Scenarios**:

1. **Given** a ticket with an AI draft and Gmail connected, **When** the brand owner clicks
   "Approve & Send", **Then** the email is delivered to the customer and the ticket status
   becomes "resolved".
2. **Given** a ticket with no Gmail connected for the brand, **When** approve is clicked,
   **Then** the UI displays: "No Gmail connected for this brand. Go to Brands → Connect Gmail
   first."
3. **Given** a successful send, **When** the UI refreshes, **Then** the ticket shows
   "Email sent" with a timestamp.

---

### User Story 3 — Email Worker Stays Running (Priority: P1)

The background email polling worker runs continuously without crashing, even when Gmail
credentials are temporarily invalid, network is unavailable, or an individual message fails to
parse. Errors are logged but the process does not exit.

**Why this priority**: A crashing worker means no new customer emails are ever received,
silently breaking the entire inbound pipeline.

**Independent Test**: Force a Gmail API error by temporarily revoking credentials; observe the
worker logs the error, sleeps 5 seconds, and resumes polling — the process PID does not
change.

**Acceptance Scenarios**:

1. **Given** the email worker is running, **When** a Gmail API call throws an exception,
   **Then** the error is logged, the worker sleeps 5 seconds, and resumes polling.
2. **Given** the email worker is running, **When** a single message fails to parse, **Then**
   only that message is skipped and subsequent messages are processed normally.
3. **Given** the worker has been running for 1 hour with intermittent errors, **Then** the
   worker process is still alive and processing new messages.

---

### User Story 4 — Brand Owner Uploads Knowledge Base Documents (Priority: P2)

A brand owner navigates to Settings → Knowledge Base, types or pastes text content with a
title, clicks Upload, and the document becomes searchable for AI responses. They can see all
uploaded sources listed and delete any source.

**Why this priority**: Without a knowledge base, the AI generates generic responses. The RAG
engine is built but has no UI — brand owners cannot feed it brand-specific content.

**Independent Test**: Upload a document containing a unique phrase, submit a test ticket asking
about that topic, and verify the AI response references the uploaded content.

**Acceptance Scenarios**:

1. **Given** the Settings → Knowledge Base tab, **When** a brand owner enters a title and
   content and clicks Upload, **Then** the source appears in the list within 3 seconds.
2. **Given** an existing knowledge base source, **When** the brand owner clicks Delete, **Then**
   the source is removed from the list and no longer used in AI responses.
3. **Given** an empty knowledge base, **When** the tab loads, **Then** an empty state is
   shown with a prompt to upload the first document.

---

### User Story 5 — New Brand Owner Completes Onboarding (Priority: P2)

A brand owner signs up, lands on the dashboard, and immediately sees a 3-step wizard instead
of a blank screen. Step 1 guides them to connect their Shopify store. Step 2 guides them to
connect Gmail. Step 3 confirms setup is complete. The wizard disappears once a brand is
connected.

**Why this priority**: New users with no brands connected see an empty, confusing dashboard.
The wizard provides immediate direction and reduces abandonment.

**Independent Test**: Create a new account with no brands, verify the wizard appears, complete
step 1, verify step 2 appears, complete step 2, verify the wizard closes and the dashboard
shows brand data.

**Acceptance Scenarios**:

1. **Given** a logged-in user with zero connected brands, **When** the dashboard loads,
   **Then** the onboarding wizard is shown instead of the empty dashboard.
2. **Given** the wizard on step 1, **When** the user completes Shopify connection, **Then**
   the wizard advances to step 2 automatically.
3. **Given** the wizard on step 2, **When** the user completes Gmail connection, **Then** the
   wizard closes and the normal dashboard is shown.
4. **Given** a user who already has a connected brand, **When** they visit the dashboard,
   **Then** the wizard is not shown.

---

### User Story 6 — Customer Reply Adds to Existing Ticket Thread (Priority: P2)

When a customer replies to an email they received from Resolv, their reply is appended to the
original ticket — not created as a new ticket. The brand owner sees the full conversation
thread in the ticket detail view.

**Why this priority**: Without threading, every customer reply creates a new orphan ticket.
Brand owners lose context and must manually reconcile conversations.

**Independent Test**: Submit a ticket, approve and send a reply, reply to that email from the
customer address, and verify the admin console shows both messages in the same ticket thread.

**Acceptance Scenarios**:

1. **Given** an existing ticket with a sent reply, **When** the customer replies to that email,
   **Then** the reply is added to the existing ticket, not created as a new ticket.
2. **Given** a reply from an unknown thread (no matching `gmail_thread_id`), **When** it
   arrives, **Then** a new ticket is created as normal.

---

### User Story 7 — Brand Owner Receives Browser Notifications (Priority: P3)

When a new ticket arrives or a new action needs approval, the brand owner's browser shows a
native notification even if the tab is in the background. Clicking the notification navigates
to the relevant ticket or action.

**Why this priority**: Brand owners cannot watch the tab all day. Notifications enable
responsive support without constant monitoring.

**Independent Test**: Grant notification permission, submit a test ticket from another browser
tab, and verify the OS notification appears within 10 seconds.

**Acceptance Scenarios**:

1. **Given** notification permission granted, **When** a new ticket arrives, **Then** a
   browser notification shows the customer name and subject within 10 seconds.
2. **Given** notification permission granted, **When** a new action needs approval, **Then**
   a browser notification shows the action type and customer name.
3. **Given** the brand owner clicks the notification, **Then** the browser focuses the app
   and navigates to the relevant ticket or action.
4. **Given** notification permission not yet granted, **When** the brand owner first logs in,
   **Then** the app requests notification permission with a brief explanation.

---

### Edge Cases

- What happens when a brand owner's Gmail token expires mid-session? The UI must show a
  reconnect prompt, not a silent failure.
- What happens if the same customer sends two emails within seconds? Both must be matched
  against thread IDs independently without race conditions.
- What happens if a knowledge base document is extremely long? The upload must either accept
  it or return a clear size limit error — not silently truncate.
- What happens when browser notification permission is denied? The app must not repeatedly
  ask; show a persistent in-app badge instead.
- What happens if the onboarding wizard is closed mid-way? It must reappear on next login
  until at least one brand is connected.

---

## Requirements *(mandatory)*

### Functional Requirements

**Auth (Item A)**
- **FR-001**: All protected backend routes MUST accept v1 JWT tokens issued by
  `/api/v1/auth/login`. A valid v1 token MUST never result in a 401 on any route the admin
  console uses.
- **FR-002**: The auth middleware MUST NOT require Supabase Auth tokens on any route
  consumed by the admin console or web form.

**Dashboard Data (Item B)**
- **FR-003**: The dashboard MUST display the true count of open/active tickets from the
  database, refreshed on each page load.
- **FR-004**: The dashboard MUST display AI Handled % calculated from actual ticket and
  action records — not a hardcoded or estimated value.

**Ticket List Fields (Item C)**
- **FR-005**: The ticket list API response MUST include `channel` and `customer_email` for
  every ticket record.
- **FR-006**: The ticket list UI MUST render a Channel column and a Sender column using those
  values. Both columns MUST never be empty for tickets that have this data.

**Worker Stability (Item D)**
- **FR-007**: The email polling worker MUST wrap its main loop body in a try/except, log the
  full exception, sleep 5 seconds on any error, and continue. The process MUST NOT exit on
  any recoverable error.
- **FR-008**: A single malformed or unparseable email MUST NOT prevent subsequent emails from
  being processed.

**Email Send (Item E)**
- **FR-009**: The approve-ai and respond endpoints MUST trigger Gmail send via the brand's
  connected Gmail account and return success or error within 10 seconds.
- **FR-010**: If Gmail is not connected for the brand, the API MUST return a descriptive error
  and the UI MUST display it.
- **FR-011**: On successful send, the ticket MUST be updated to status "resolved" and
  `email_sent: true`.

**Knowledge Base UI (Item F)**
- **FR-012**: The Settings page MUST include a Knowledge Base tab with a title field, content
  area, Upload button, source list, and Delete button per source.
- **FR-013**: The Upload action MUST call the backend and display the new source in the list
  without a full page reload.
- **FR-014**: The Delete action MUST remove the source from the backend and from the list
  immediately.

**Onboarding Wizard (Item G)**
- **FR-015**: When a logged-in user has zero connected brands, the dashboard MUST display the
  3-step onboarding wizard.
- **FR-016**: The wizard MUST progress through: (1) Connect Shopify, (2) Connect Gmail,
  (3) Done. Each step MUST link to the relevant connection flow.
- **FR-017**: The wizard MUST not appear for users who already have at least one brand
  connected.

**Email Threading (Item H)**
- **FR-018**: When an inbound email has a `gmail_thread_id` matching an existing ticket, the
  email body MUST be appended to that ticket — no new ticket is created.
- **FR-019**: Inbound emails with no matching thread MUST create new tickets as before.

**Browser Notifications (Item I)**
- **FR-020**: The admin console MUST request browser notification permission on first login.
- **FR-021**: When a new ticket is created or a new pending action is detected, a browser
  notification MUST be shown with a relevant summary.
- **FR-022**: Clicking a notification MUST navigate the browser to the relevant ticket or
  action page.

### Key Entities

- **Ticket**: Customer support request. Key fields: `id`, `status`, `channel`,
  `customer_email`, `customer_name`, `subject`, `ai_draft`, `email_sent`, `gmail_thread_id`,
  `brand_id`, `created_at`.
- **Action**: Proposed financial operation requiring approval. Key fields: `id`,
  `action_type` (refund/cancel/modify), `status` (pending/approved/rejected), `tenant_id`,
  `customer_email`, `order_id`.
- **Brand**: Shopify store connected to Resolv. Key fields: `id`, `shopify_domain`,
  `gmail_connected`, `tenant_id`.
- **Knowledge Base Source**: Uploaded document for RAG. Key fields: `id`, `title`, `content`,
  `brand_id`, `created_at`.
- **Tenant**: Brand owner account. Key fields: `id`, `email`, `company_name`.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A brand owner can log in and access all dashboard, ticket, and action screens
  without being redirected to login due to auth failure.
- **SC-002**: Dashboard Active Conversations count matches the number of open tickets in the
  database with zero discrepancy.
- **SC-003**: 100% of tickets in the list show a non-empty Channel and Sender value where
  that data exists in the database.
- **SC-004**: The email polling worker runs continuously for 24 hours without the process
  exiting, even when Gmail errors occur.
- **SC-005**: Clicking "Approve & Send" on a ticket with Gmail connected delivers the email
  to the customer inbox within 30 seconds in 95% of attempts.
- **SC-006**: A brand owner completes the full knowledge base CRUD cycle (upload, view,
  delete) in under 60 seconds.
- **SC-007**: A new brand owner with zero brands sees the onboarding wizard and can complete
  all 3 steps without leaving the app.
- **SC-008**: Customer replies to existing email threads appear in the original ticket in
  100% of cases where a matching `gmail_thread_id` exists.
- **SC-009**: A browser notification appears within 10 seconds of a new ticket being created,
  when the brand owner has granted notification permission.

---

## Assumptions

- The Gmail OAuth credentials and `brand_gmail_service` are implemented and functional for
  sending; only the call path from approve/respond endpoints needs fixing.
- The `gmail_thread_id` field already exists on the tickets table in Supabase.
- The v1 tenant auth system is the sole auth system for all admin console routes — no
  Supabase Auth required.
- The knowledge base backend endpoints already exist and function correctly; only the UI is
  missing.
- Browser notifications use the standard Web Notifications API — no third-party push service
  needed.
- No new npm or Python packages are to be installed; all implementations use currently
  installed dependencies.
