# Feature Specification: Shopify Customer Support AI

**Feature Branch**: `002-shopify-customer-support`
**Created**: 2026-04-08
**Status**: Draft
**Input**: User description: "Build a multi-tenant AI-powered customer support system for Shopify e-commerce brands.

The system focuses on handling customer emails and webform requests, automatically responding to simple queries and preparing operational actions (refunds, cancellations, address changes) for human approval.

Core goals:
- Replace traditional support teams with an AI system that resolves customer issues
- Minimize human involvement to only approving sensitive actions
- Provide fast, reliable, and scalable support for e-commerce brands"

Auth: Supabase Auth
Email Ingest: Email forwarding (catch-all with polling)
AI Provider: Mistral AI
Reply Delivery: SMTP sending

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Submit Support Request via Web Form (Priority: P1)

As a customer, I want to submit support requests through a web form so that I can get help without needing to use email. The form should accept my email, order number (optional), subject, and message, then provide me with a ticket ID and status.

**Why this priority**: This provides the foundational capability for customers to reach support through a direct channel and establishes the ticketing system that all other channels will use.

**Independent Test**: Can be fully tested by filling out the web form and verifying that a ticket is created in the system with the correct details and a unique ticket ID is returned to the user.

**Acceptance Scenarios**:

1. **Given** customer is on the support page, **When** customer fills out all required fields and submits the form, **Then** a ticket is created in the database and customer receives a ticket ID and status confirmation
2. **Given** customer enters invalid email format, **When** customer submits the form, **Then** appropriate validation error is shown and form is not submitted

---

### User Story 2 - Email Forwarding and Ticket Creation (Priority: P1)

As a brand using the system, I want my customers to send emails to a designated support address so that their inquiries automatically become tickets in the system without manual intervention.

**Why this priority**: Email is the primary communication channel for e-commerce customers, and automating ticket creation from email is essential for the core value proposition.

**Independent Test**: Can be tested by forwarding an email to the system's catch-all address and verifying that a ticket is created with correct customer email, subject, and body parsed correctly.

**Acceptance Scenarios**:

1. **Given** customer sends email to brand's support address, **When** the email is received and processed, **Then** a ticket is created with customer email, subject, message body, and timestamp
2. **Given** customer sends email with an order number, **When** the email is processed, **Then** the ticket is linked to the corresponding order in Shopify (if found)

---

### User Story 3 - AI Processing and Auto-Reply (Priority: P1)

As a customer who sent a simple inquiry, I want to receive an immediate AI-generated response so that my question is answered without human involvement.

**Why this priority**: This is the core value proposition - the AI resolves simple queries automatically, reducing human workload.

**Independent Test**: Can be tested by sending an email with a simple question and verifying that an AI response is generated and sent back via SMTP within 2 minutes.

**Acceptance Scenarios**:

1. **Given** customer sends email with a simple question (e.g., "When will my order ship?"), **When** the email is processed by the AI engine, **Then** the system detects the simple intent, retrieves relevant information from the RAG knowledge base, and sends an auto-reply
2. **Given** customer sends email that requires order lookup, **When** the email contains an order number, **Then** the AI retrieves the order from Shopify before generating a response
3. **Given** the AI cannot confidently answer the question, **When** processing completes, **Then** the ticket is marked for human review instead of sending an auto-reply

---

### User Story 4 - Intent Detection and Action Proposal Creation (Priority: P1)

As a brand using the system, I want the AI to detect when a customer request involves sensitive operations (refunds, cancellations, address changes) so that these can be reviewed by a human before execution.

**Why this priority**: This ensures that financial and operational actions have proper human oversight, preventing costly AI mistakes.

**Independent Test**: Can be tested by sending an email requesting a refund and verifying that an action proposal is created instead of an auto-reply.

**Acceptance Scenarios**:

1. **Given** customer sends email requesting a refund, **When** the AI processes the email, **Then** the system detects the refund intent, creates an action proposal with type "refund", order ID, and confidence score, and places it in the Action Queue
2. **Given** customer sends email requesting order cancellation, **When** the AI processes the email, **Then** the system detects the cancellation intent and creates an action proposal with type "cancel"
3. **Given** customer requests an address change, **When** the AI processes the email, **Then** the system detects the address change intent and creates an action proposal with type "address_change"

---

### User Story 5 - Action Queue and One-Click Approval (Priority: P1)

As a brand user, I want to see all pending action proposals in a queue so that I can quickly review and approve or reject them with a single click.

**Why this priority**: This is the main workflow for human oversight - approving or rejecting AI-proposed actions before they execute in Shopify.

**Independent Test**: Can be tested by viewing the Action Queue and verifying that pending actions are displayed with clear details, then clicking approve/reject and verifying the action executes.

**Acceptance Scenarios**:

1. **Given** there are pending action proposals in the queue, **When** the user views the Action Queue, **Then** all pending actions are displayed with order ID, action type, customer email, confidence score, and risk level
2. **Given** user clicks "Approve" on a refund action, **When** the approval is processed, **Then** the refund is executed via Shopify API and the action status changes to "executed"
3. **Given** user clicks "Reject" on an action proposal, **When** the rejection is processed, **Then** the action status changes to "rejected" and the customer is notified of the rejection
4. **Given** the Shopify API returns an error during execution, **When** the action is executed, **Then** the action status changes to "failed" with error details visible to the user

---

### User Story 6 - Shopify Integration - Order Retrieval (Priority: P1)

As the system, I need to retrieve order information from a tenant's Shopify store so that the AI can provide accurate responses about order status, items, and details.

**Why this priority**: Accessing Shopify order data is essential for the AI to answer order-related questions and to identify correct orders for action proposals.

**Independent Test**: Can be tested by making an API call to retrieve an order by ID and verifying that order details (status, items, customer, shipping address) are returned correctly.

**Acceptance Scenarios**:

1. **Given** a tenant has connected their Shopify store, **When** the system needs to look up an order by order number, **Then** the order is retrieved from Shopify using the Admin API
2. **Given** the order number does not exist in Shopify, **When** the system attempts to retrieve it, **Then** an appropriate error is returned and the ticket is flagged for human review

---

### User Story 7 - Shopify Integration - Execute Actions (Priority: P1)

As the system, I need to execute operational actions (refunds, cancellations, address changes) in Shopify when a user approves an action proposal.

**Why this priority**: This is the final step in the workflow - actually performing the approved action in the customer's Shopify store.

**Independent Test**: Can be tested by approving a refund action and verifying that the refund is created in Shopify with correct amount.

**Acceptance Scenarios**:

1. **Given** user approves a refund action, **When** the action is executed, **Then** a refund is created in Shopify via the Admin API for the correct order and amount
2. **Given** user approves an order cancellation, **When** the action is executed, **Then** the order is cancelled in Shopify
3. **Given** user approves an address change, **When** the action is executed, **Then** the shipping address is updated in Shopify
4. **Given** the Shopify API returns an error during action execution, **When** the action fails, **Then** the failure is logged and the user is notified with error details

---

### User Story 8 - Dashboard - Inbox View (Priority: P2)

As a brand user, I want to see all incoming tickets in an inbox view so that I can quickly scan and identify which tickets need attention.

**Why this priority**: The inbox is the primary view for monitoring incoming customer requests and their status.

**Independent Test**: Can be tested by viewing the inbox and verifying that tickets are displayed with customer email, subject, status, and timestamp.

**Acceptance Scenarios**:

1. **Given** there are multiple tickets in the system, **When** the user views the inbox, **Then** tickets are displayed sorted by newest first, with status indicators (new, processing, responded, needs_review)
2. **Given** a ticket has been processed by the AI, **When** the user views the ticket in the inbox, **Then** the ticket shows the AI's decision (auto-replied or action proposed)

---

### User Story 9 - Dashboard - History View (Priority: P3)

As a brand user, I want to see a history of all actions and replies so that I can review past decisions and audit system behavior.

**Why this priority**: History provides transparency and audit capability for the brand to review what the AI has done and what actions have been taken.

**Independent Test**: Can be tested by viewing the history and verifying that past actions and replies are displayed with timestamps and outcomes.

**Acceptance Scenarios**:

1. **Given** the system has processed tickets and actions, **When** the user views the history, **Then** all past actions (approved/rejected/executed/failed) and AI replies are displayed with timestamps
2. **Given** user wants to see details of a specific past action, **When** they click on the action in history, **Then** full details including the original customer request, AI analysis, and outcome are displayed

---

### User Story 10 - Dashboard - Settings (Priority: P2)

As a brand user, I want to configure my store settings so that I can connect my Shopify store and email account to the system.

**Why this priority**: Settings are required for the system to function - the brand needs to input their Shopify credentials and email configuration.

**Independent Test**: Can be tested by entering Shopify credentials and email settings, then verifying that the system can successfully connect to both.

**Acceptance Scenarios**:

1. **Given** user is on the settings page, **When** they enter their Shopify store URL and access token, **Then** the system validates the credentials and confirms successful connection
2. **Given** user enters invalid Shopify credentials, **When** they save the settings, **Then** an error message is displayed explaining the issue
3. **Given** user configures their email settings (SMTP credentials, support email address), **When** the settings are saved, **Then** the system can send emails on behalf of the brand

---

### User Story 11 - Multi-Tenant Data Isolation (Priority: P1)

As a brand using the system, I want to ensure that my data is completely isolated from other brands so that my customer information and business data are secure.

**Why this priority**: Multi-tenant isolation is a fundamental requirement for a SaaS product - brands must not be able to see each other's data.

**Independent Test**: Can be tested by creating data as Brand A and verifying that Brand B cannot see or access Brand A's tickets, actions, or settings.

**Acceptance Scenarios**:

1. **Given** Brand A has created tickets and actions, **When** Brand B logs into the system, **Then** Brand B sees only their own tickets and actions, not Brand A's data
2. **Given** Brand A has configured their Shopify store, **When** Brand B accesses the system, **Then** Brand B cannot see Brand A's Shopify credentials or access their store

---

### User Story 12 - RAG Knowledge Base Management (Priority: P2)

As a brand user, I want to add and manage my brand's knowledge base content so that the AI has accurate information to answer customer questions.

**Why this priority**: The RAG knowledge base is what allows the AI to provide accurate, brand-specific answers - brands need to be able to manage this content.

**Independent Test**: Can be tested by adding a new knowledge base article and verifying that the AI uses it when answering related questions.

**Acceptance Scenarios**:

1. **Given** user adds a new knowledge base article with title and content, **When** the article is saved, **Then** it becomes available for the AI to retrieve via RAG search
2. **Given** user updates an existing knowledge base article, **When** the update is saved, **Then** the AI uses the updated content for future queries

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST receive incoming emails via a catch-all forwarding address that is polled periodically
- **FR-002**: System MUST parse forwarded emails to extract sender email, subject, body, and any order numbers mentioned
- **FR-003**: System MUST create a ticket for each incoming email with status "new"
- **FR-004**: System MUST accept web form submissions and create tickets similar to email processing
- **FR-005**: System MUST use Mistral AI to detect intent (refund_request, cancel_request, address_change_request, question, other)
- **FR-006**: System MUST analyze sentiment and urgency of incoming messages
- **FR-007**: System MUST retrieve relevant information from the brand's RAG knowledge base before generating responses
- **FR-008**: System MUST generate response drafts using Mistral AI with brand-specific context
- **FR-009**: If request is simple (question intent with high confidence), system MUST send auto-reply via SMTP
- **FR-010**: If request involves money or risk (refund, cancel, address change), system MUST create an action proposal instead of auto-replying
- **FR-011**: Action proposals MUST include: action_type, order_id, customer_email, confidence_score, risk_level, original_message, suggested_operation
- **FR-012**: System MUST display pending actions in the Action Queue with all relevant details
- **FR-013**: User MUST be able to approve or reject action proposals with one click
- **FR-014**: Upon approval, system MUST execute the action in Shopify via Admin API
- **FR-015**: System MUST validate Shopify API responses before marking actions as executed
- **FR-016**: Upon rejection, system MUST log the rejection and optionally notify the customer
- **FR-017**: System MUST connect to tenant's Shopify store using store URL and access token
- **FR-018**: System MUST retrieve order information from Shopify to answer order-related queries
- **FR-019**: System MUST create refunds in Shopify via the Refunds API
- **FR-020**: System MUST cancel orders in Shopify via the Orders API
- **FR-021**: System MUST update shipping addresses in Shopify via the Orders API
- **FR-022**: System MUST implement multi-tenant isolation - every request and query MUST include tenant_id
- **FR-023**: System MUST ensure no cross-tenant data leakage at the application and database layers
- **FR-024**: System MUST encrypt sensitive data (Shopify tokens, email credentials) at rest
- **FR-025**: System MUST track all events: email received, AI processed, action created, approved/rejected, executed/failed
- **FR-026**: System MUST display tickets in the Inbox with status indicators
- **FR-027**: System MUST display history of all actions and replies
- **FR-028**: System MUST allow users to configure Shopify and email settings
- **FR-029**: System MUST allow users to add and manage knowledge base content for RAG
- **FR-030**: System MUST handle Shopify API errors gracefully without crashing
- **FR-031**: System MUST implement retry logic with exponential backoff for transient Shopify API failures

### Key Entities *(include if feature involves data)*

- **Tenant**: Represents a brand/account in the multi-tenant system
- **Ticket**: Customer support request created from email or web form
- **Action**: Proposed operational action (refund, cancel, address_change) requiring approval
- **Customer**: Customer information extracted from emails (email, name if available)
- **Order**: Shopify order data retrieved for AI context and action execution
- **KnowledgeBase**: Brand-specific content for RAG retrieval
- **AuditLog**: Event tracking for transparency

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Customers can submit support requests via web form and receive immediate ticket ID confirmation within 5 seconds
- **SC-002**: AI agent responds to 80% of simple customer inquiries (questions, order status queries) with auto-replies without human involvement
- **SC-003**: Action proposals are created within 30 seconds of receiving emails that require sensitive operations
- **SC-004**: Users can approve or reject actions with one click, and approved actions execute in Shopify within 10 seconds
- **SC-005**: System maintains 99.9% uptime and does not crash on Shopify API errors or email processing failures
- **SC-006**: Multi-tenant isolation is enforced - cross-tenant data access is prevented
- **SC-007**: All events are logged and visible in the History view for audit purposes
- **SC-008**: Dashboard loads within 2 seconds and provides real-time updates for Action Queue