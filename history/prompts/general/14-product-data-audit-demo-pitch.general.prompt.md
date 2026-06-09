---
id: 14
title: Product Data Audit for Demo and Pitch
stage: general
date: 2026-06-02
surface: agent
model: claude-sonnet-4-6
feature: none
branch: 006-email-guardian
user: Syeda Hafsa
command: RESOLV — EXTRACT PRODUCT DATA FOR DEMOS AND PITCHING
labels: ["product","audit","demo","pitch","database","metrics","features"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - resolv_product_data.md
tests:
  - manual: open resolv_product_data.md and verify numbers match what you see in Supabase dashboard
  - manual: the 86.5% AI handled rate is verifiable by checking tickets table status column
  - manual: the 299 email_filter_log entries are verifiable in Supabase table viewer
---

## Prompt

RESOLV — EXTRACT PRODUCT DATA FOR DEMOS AND PITCHING

Task 1: Product capability audit (code reading)
Task 2: Database metrics (SQL queries against live Supabase)
Task 3: Feature verification checklist
Task 4: Generate resolv_product_data.md

## Response snapshot

### Database (live, queried 2026-06-02)
- 52 total tickets, 86.5% AI-handled, avg confidence 67.1/100
- 299 emails evaluated, 179 blocked (59.9%), 120 passed
- 11 actions staged (all cancel_order), 3 brands, 2 Gmail connected, 1 Shopify
- 0 emails in quarantine

### Key code facts
- Polling: 60s interval (EMAIL_POLL_INTERVAL env)
- Model: mistral-large-latest (MISTRAL_MODEL env)
- Persona: "Luna"
- Auto-reply threshold: 65% confidence (message_processor)
- Guardian threshold: 75% (system_settings default)
- Email body: Gmail snippet ~160 chars (not full MIME) — noted as PARTIAL
- Shopify API version: 2024-01
- RAG: mistral-embed, top_k=3, similarity threshold=0.5

### Feature verification
- 35 features WORKING
- 4 PARTIAL (email body extraction, cancel/refund Shopify execution, quarantine)
- 9 NOT IMPLEMENTED (WhatsApp, web form tickets, order reship, exchange, CSAT, SLA alerts, bulk ops, analytics)

## Outcome

- ✅ Impact: Full honest product data document created at resolv_product_data.md — usable directly for demo scripts, pitch slides, website copy
- 🧪 Tests: Verify numbers in Supabase dashboard
- 📁 Files: 1 file created (resolv_product_data.md)
- 🔁 Next prompts: Use resolv_product_data.md to generate demo video script; fix email body extraction (MIME parsing instead of snippet)
- 🧠 Reflection: The 86.5% AI-handled rate is real and strong. The biggest honest limitation is email body = snippet only. All 11 staged actions are cancel_order (none refund or address change) — suggesting the cancel detection is working but refund keyword patterns haven't fired yet in production.

## Evaluation notes (flywheel)

- Failure modes observed: Supabase query hit a 2000-row default limit (Resolv only has 52 tickets so this was fine, but should use pagination for large datasets)
- Graders run and results (PASS/FAIL): Database query ran successfully via Python script using backend's supabase_client
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): Fix email body extraction to use MIME parts instead of Gmail snippet — this would unlock full-text AI understanding of long emails
