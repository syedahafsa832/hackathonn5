---
id: 3
title: Customer Success AI Agent Plan
stage: plan
date: 2026-02-03
surface: agent
model: claude-sonnet-4-5-20250929
feature: customer-success-agent
branch: 001-customer-success-agent
user: default_user
command: /sp.plan
labels: [plan, ai, customer-success, multi-channel, architecture]
links:
  spec: specs/001-customer-success-agent/spec.md
  plan: specs/001-customer-success-agent/plan.md
  ticket: null
  adr: null
  pr: null
files:
 - specs/001-customer-success-agent/plan.md
 - specs/001-customer-success-agent/research.md
 - specs/001-customer-success-agent/data-model.md
 - specs/001-customer-success-agent/quickstart.md
 - specs/001-customer-success-agent/contracts/customer-success-agent-openapi.yaml
 - .specify/memory/constitution.md
tests:
 - none
---

## Prompt

Implementation architecture:

PHASE 1 - INCUBATION (Hours 1-16):
Technology: Claude Code for rapid prototyping
- Build prototype agent in Python
- Create MCP server with tools (search_kb, create_ticket, escalate, send_response)
- Test with sample tickets from all three channels
- Document discovered requirements and edge cases
- Create channel-specific response templates
- Output: Working prototype + specs/discovery-log.md

PHASE 2 - DATABASE DESIGN (Hours 17-19):
Technology: PostgreSQL 16 with pgvector extension
Schema includes:
- customers table (id, email, phone, name, metadata)
- customer_identifiers (for cross-channel matching)
- conversations table (customer_id, initial_channel, status, sentiment)
- messages table (conversation_id, channel, direction, content, delivery_status)
- tickets table (customer_id, source_channel, category, priority, status)
- knowledge_base table (title, content, embedding vector)
- channel_configs, agent_metrics tables

PHASE 3 - CHANNEL INTEGRATIONS (Hours 20-24):
Technology: Python with async/await

Gmail Handler (channels/gmail_handler.py):
- Gmail API client with OAuth2
- Pub/Sub webhook receiver
- Email parsing (extract body, from, subject, thread_id)
- Reply sender with threading support
- Response formatter (add greeting, signature)

WhatsApp Handler (channels/whatsapp_handler.py):
- Twilio REST client
- Webhook signature validation
- Message parsing (from Twilio form data)
- Message sender via Twilio API
- Response formatter (split long messages, keep under 1600 chars)

Web Form (React/Next.js component + FastAPI endpoint):
Frontend (web-form/SupportForm.jsx):
- React form with validation
- Category/priority dropdowns
- Loading states and error handling
- Success screen with ticket ID
- Styling with Tailwind CSS

Backend (channels/web_form_handler.py):
- FastAPI router with Pydantic validation
- POST /support/submit endpoint
- GET /support/ticket/{id} for status
- Publish to Kafka on submission

PHASE 4 - AGENT IMPLEMENTATION (Hours 25-29):
Technology: OpenAI Agents SDK with grok-beta

Tools (agent/tools.py):
Each tool uses @function_tool decorator with Pydantic input validation:
1. search_knowledge_base: Vector similarity search with pgvector
2. create_ticket: Insert to tickets table with channel tracking
3. get_customer_history: Cross-channel history query
4. escalate_to_human: Update ticket + publish Kafka event
5. send_response: Route to appropriate channel handler

Agent Definition (agent/customer_success_agent.py):
- System prompt with channel awareness
- Hard constraints (NEVER discuss pricing, etc.)
- Escalation triggers
- Channel-specific formatting instructions
- Tool workflow: create_ticket → get_history → search_kb → send_response

PHASE 5 - EVENT STREAMING (Hours 30-32):
Technology: Apache Kafka (Confluent Cloud recommended)

Topics:
- fte.tickets.incoming: Unified intake from all chaPOST /webhooks/whatsapp/status: Delivery status updates
- POST /support/submit: Web form submission
- GET /support/ticket/{id}: Ticket status
- GET /conversations/{id}: Full conversation history
- GET /customers/lookup: Customer lookup by email/phone
- GET /metrics/channels: Channel-specific metrics
- GET /health: Health check

Middleware:
- CORS for web form
- Request ID tracking
- Error handling with proper status codes

PHASE 8 - KUBERNETES DEPLOYMENT (Hours 41-45):
Technology: Kubernetes with HPA

Manifests (k8s/):
- namespace.yaml: customecust

Test Suites:
1. Unit Tests (test_agent.py, test_tools.py):
   - Tool input validation
   - Agent decision logic
   - Channel formatting

2. Integration Tests (test_channels.py):
   - Gmail webhook processing
   - WhatsApp webhook validation
   - Web form submission + retrieval

3. E2E Tests (test_multichannel_e2e.py):
   - Full flow per channel
   - Cross-channel customer identification
   - Ticket creation and status tracking

4. Load Tests (load_test.py with Locust):
   - 100+ web form submissions/24h
   - 50+ emails/24h
   - 50+ WhatsApp messages/24h
   - Monitor P95 latency < 3s

DEPLOYMENT STRATEGY:
- Local dev: docker-compose with all services
- Staging: minikube or cloud K8s
- Production: Cloud provider (AWS EKS, GCP GKE, Azure AKS)

MONITORING:
- Prometheus metrics from FastAPI
- Grafana dashboards (channel metrics, latency, escalation rate)
- AlertManager for critical issues

CRITICAL: This is a PostgreSQL-based CRM system, NOT an integration with external CRMs

## Response snapshot

Completed comprehensive planning for the Customer Success AI Agent, including: technical context, constitution check validation, project structure, research findings, data model, quickstart guide, and API contracts. The plan validates all constitution principles are met and provides detailed specifications for implementation across all channels (Gmail, WhatsApp, Web Form) with PostgreSQL as the central CRM system.

## Outcome

- ✅ Impact: Complete architecture plan for multi-channel AI customer success agent with all constitution principles validated
- 🧪 Tests: No tests required for planning phase
- 📁 Files: Created plan.md, research.md, data-model.md, quickstart.md, and API contract specification
- 🔁 Next prompts: Ready for task breakdown with /sp.tasks
- 🧠 Reflection: Plan comprehensively addresses all requirements with proper architecture and compliance

## Evaluation notes (flywheel)

- Failure modes observed: None
- Graders run and results (PASS/FAIL): N/A
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): Begin implementation with task breakdown
