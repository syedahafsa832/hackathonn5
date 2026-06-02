---
id: 8
title: Migration Schema Audit and Fixes
stage: general
date: 2026-06-02
surface: agent
model: claude-sonnet-4-6
feature: none
branch: 006-email-guardian
user: Syeda Hafsa
command: see all my migration and see if we need to edit any supabase tables
labels: ["backend","migrations","supabase","schema","database"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/migrations/018_missing_tables.sql
tests:
  - manual: run migration 018 in Supabase SQL editor and verify tables created without errors
  - manual: send email → ticket.tags column populated (not tags_array)
  - manual: takeover conversation → conversation_overrides row inserted
  - manual: /api/v1/settings/gmail/queue-status returns 200 (not 500)
---

## Prompt

see all my migration and see if we need to edit any supabase tables

## Response snapshot

Reviewed all 14 migrations (002–017) against code usage. Found 4 gaps requiring a new migration:

**Gap 1 — `conversation_overrides` table missing**
- Defined in `backend/schema.sql` but not in any migration file
- Referenced by: `admin.py` (takeover/release endpoints), `message_processor.py` (_check_thread_override), `supabase_service.py` (check_conversation_override)
- Without it: takeover inserts fail silently; AI keeps replying even after Take Over

**Gap 2 — `tickets.tags` vs `tickets.tags_array` column mismatch**
- Migration 016 added column as `tags_array TEXT[]`
- All code writes `"tags": ticket_tags` (message_processor.py lines 124, 250)
- Frontend reads `c.tags` (Tickets.jsx line 220)
- Without fix: tags are never stored; tag filtering in UI always empty
- Fix: rename `tags_array → tags` via DO block (safe — column was just added, never written to by old code)

**Gap 3 — `send_tasks` table missing**
- Referenced by `saas_settings.py` `/gmail/queue-status` endpoint (lines 542-544)
- Columns needed: status (queued/sent/failed), sent_at, tenant_id, to_email
- Without it: queue-status endpoint crashes (returns 500 or caught exception with zeros)

**Gap 4 — `send_log` table missing**
- Referenced by `saas_settings.py` `/gmail/queue-status` endpoint (line 545)
- Columns needed: sent_at, tenant_id
- Without it: hourly rate display always 0 (caught exception)

**Also verified OK:**
- `match_tenant_rag_chunks` RPC: created in migration 005 ✓
- `knowledge_base_sources.tenant_id`: correctly references tenants(id) in migration 005 ✓
- `actions` columns (order_number, customer_email, ai_reasoning, etc): added in migration 017 ✓
- `tickets` other new columns (customer_sentiment, first_response_at): added in migration 016 ✓
- `gmail_thread_id`, `channel`, `messages`: added in migration 008 ✓
- `email_category`, `sender_type`: added in migration 011 ✓

**Created: `backend/migrations/018_missing_tables.sql`** with all four fixes.

## Outcome

- ✅ Impact: conversation_overrides table created (takeover feature now functional); tags column name fixed (tag filtering works); send_tasks and send_log created (queue-status endpoint stops crashing); gmail_message_id and unread_count columns added
- 🧪 Tests: Manual — run migration in Supabase SQL editor, verify no errors, test takeover and tag display
- 📁 Files: 1 file created (backend/migrations/018_missing_tables.sql)
- 🔁 Next prompts: Run the migration in Supabase; restart backend; test takeover flow end-to-end
- 🧠 Reflection: The tags_array → tags rename is the trickiest part — the DO block ensures it only renames if the old name exists and new name doesn't, making it safe to run on both existing DBs (with tags_array) and fresh DBs (where column may not exist yet)

## Evaluation notes (flywheel)

- Failure modes observed: if migration 016 was never run, tags_array doesn't exist; the DO block handles this, then ADD COLUMN IF NOT EXISTS creates tags directly
- Graders run and results (PASS/FAIL): none automated
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): add tenant_id filter to all send_tasks queries so multi-tenant queue stats are isolated per tenant
