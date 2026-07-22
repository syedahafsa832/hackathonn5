---
id: 45
title: Resolv Production Fixes
stage: general
date: 2026-07-22
surface: agent
model: GPT-5
feature: none
branch: main
user: Zohaib
command: N/A - implement production fixes in existing backend
labels: [gmail,tickets,polling,replay,production]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - D:\hack5\hack5\backend\src\api\routes\tickets.py
  - D:\hack5\hack5\backend\src\channels\email_poller.py
  - D:\hack5\hack5\backend\src\workers\message_processor.py
  - D:\hack5\hack5\backend\src\services\supabase_service.py
  - D:\hack5\hack5\backend\main.py
tests:
  - Not run yet
---

## Prompt

You are a senior backend engineer implementing production fixes.

We already completed investigation.

Do NOT redesign the whole architecture.
Do NOT create new conversation systems.
Do NOT refactor unrelated code.

Implement only the following P0 fixes.

---

## Fix 1: Ticket ordering

Problem:

Tickets are sorted by created_at.

This hides active conversations because old tickets with new replies still appear old.

Change ticket listing logic so active conversations appear first.

Preferred:

Use last_message_at if available.

Otherwise use updated_at.

Requirements:

- Do not break pagination.
- Do not break existing filters.
- Keep tenant/brand filtering unchanged.
- Add/update indexes if needed.

Explain files changed.

---

## Fix 2: Conversation replay

Problem:

Dashboard shows:

"No messages in this thread"

because replay reads from ai_conversations, but production does not have that table.

Current reality:

Messages are already stored through existing email processing paths.

Find the current source of truth for:

- inbound customer emails
- AI replies
- human replies

Modify replay endpoint to correctly retrieve existing messages.

Requirements:

Replay should show:

- customer messages
- AI responses
- human responses
- timestamps
- sender information
- message direction

Do NOT create a new messages architecture yet.

Do NOT migrate tables yet.

---

## Fix 3: Gmail polling reliability

Problem:

New emails are not appearing consistently.

Improve observability and reliability.

Add:

- clear poll start logs
- brand being processed
- number of emails fetched
- successful processing count
- failures with stack traces

Check:

- exceptions being swallowed
- token refresh failures
- invalid Gmail connections

Do not change OAuth architecture.

---

## Fix 4: Multi-tenant safety checks

Before modifying anything:

Verify all Gmail/ticket queries include correct ownership filtering.

Check:

- brand_id
- tenant_id
- organization_id

Prevent:

- one user's emails appearing for another user
- one Gmail connection being reused accidentally

Do not redesign tenancy.

Only fix obvious leakage risks.

---

## After coding:

Provide:

1. Files modified
2. Why each change was needed
3. Database changes required (if any)
4. Testing steps
5. Potential risks

Keep the changes production-safe and minimal.

## Response snapshot

Acknowledged. I’ll implement only the P0 fixes in the existing backend, keeping tenancy and messaging architecture intact, and I’ll report back with the files changed, why they changed, any DB impact, and verification steps.

## Outcome

- ✅ Impact: Prompt history recorded for this implementation request.
- 🧪 Tests: Not run yet.
- 📁 Files: history/prompts/general/45-resolv-production-fixes.general.prompt.md
- 🔁 Next prompts: Production fix implementation and verification.
- 🧠 Reflection: Keep edits tightly scoped to ordering, replay, polling observability, and ownership safety.

## Evaluation notes (flywheel)

- Failure modes observed: None yet.
- Graders run and results (PASS/FAIL): Not run.
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): Patch the ticket query and replay fallback first.
