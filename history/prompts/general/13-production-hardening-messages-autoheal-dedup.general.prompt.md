---
id: 13
title: Production Hardening — Messages, Auto-Heal, Dedup
stage: general
date: 2026-06-02
surface: agent
model: claude-sonnet-4-6
feature: none
branch: 006-email-guardian
user: Syeda Hafsa
command: RESOLV — PRODUCTION HARDENING (continued from session 12)
labels: ["backend","email-loop","dedup","auto-heal","dashboard","tenant-isolation","migration"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/migrations/020_production_hardening.sql
  - backend/src/channels/email_poller.py
  - backend/src/workers/message_processor.py
  - backend/src/services/supabase_service.py
  - backend/src/api/routes/tickets.py
  - ai-ops-console/src/pages/Dashboard.jsx
tests:
  - manual: send an email → wait 2 minutes → poll logs must show "Skipping already-processed message" on 2nd cycle
  - manual: create email with "Re: Re: Re: something" subject → poller must skip it (Re: count=3)
  - manual: click ticket from Gmail notification link → must open (no "Conversation does not exist" for unlinked brands)
  - manual: dashboard refresh button → counter shows "Just updated" then increments "1s ago", "2s ago" every second
  - manual: run migration 020 in Supabase — no errors
---

## Prompt

RESOLV — PRODUCTION HARDENING (session continued)

Six fixes for production stability. Continued from session 12 where startup_heal was added to main.py.

## Response snapshot

### FIX 1: Migration 020

Created `backend/migrations/020_production_hardening.sql`:
- `ALTER TABLE tickets ADD COLUMN IF NOT EXISTS gmail_message_id TEXT;`
- `CREATE INDEX ... idx_tickets_gmail_message_id ... WHERE gmail_message_id IS NOT NULL;`
- Ensures `messages` column defaults to `'[]'::jsonb` and backfills `NULL` rows to `[]`

### FIX 5: Email loop — Re: count + gmail_message_id dedup

**`email_poller.py` `_poll_brand_inbox()`:**
Two new guards added after the own-address check:
```python
# Guard 2: deep reply chain
re_count = subject.lower().count("re:")
if re_count >= 3:
    logger.info(f"[Poller] Skipping deep reply chain (Re: count={re_count}): {subject[:60]}")
    continue

# Guard 3: already-processed Gmail message ID
if gmail_msg_id:
    already_seen = supabase_select("tickets", {"gmail_message_id": f"eq.{gmail_msg_id}"})
    if already_seen:
        continue
```

`gmail_message_id` (= `email["id"]` from Gmail API) now included in the new-ticket payload.

**`message_processor.py`:**
- Extracts `gmail_message_id = message.get('gmail_message_id')` at Stage 1
- Passes it into early ticket creation (Stage 1.8) and main ticket_payload

**`supabase_service.py` `create_ticket()`:**
- Added `"gmail_message_id": ticket_data.get("gmail_message_id")` to the insert dict

### FIX 2: Auto-heal unlinked brands in get_ticket

**`tickets.py` `get_ticket()`:**
Before raising 404, checks if the ticket's brand has no tenant_id set. If so, links it now:
```python
ticket_brand_id = ticket.get("brand_id") or ticket.get("store_id")
if brand_ids is not None and ticket_brand_id not in brand_ids:
    brand_row = supabase_select("brands", {"id": f"eq.{ticket_brand_id}"})
    if brand_row and brand_row[0].get("tenant_id") is None:
        supabase_update("brands", {"id": f"eq.{ticket_brand_id}"}, {"tenant_id": tenant.tenant_id})
        return ticket  # Allow access after auto-linking
    raise HTTPException(status_code=404, detail="Ticket not found")
```
Also fixed silent bug: ticket FK is `store_id`, not `brand_id`. The old check `ticket.get("brand_id")` always returned `None`, which caused ALL tickets to 404 for authenticated users. Fixed to `ticket.get("brand_id") or ticket.get("store_id")`.

### FIX 3: Dashboard "X seconds ago" live counter

**`Dashboard.jsx`:**
- Added `secondsAgo` state (integer, seconds since last refresh)
- Added `useEffect` with `setInterval` (1000ms) — resets on `lastRefreshed` change
- Display: `"Just updated"` at 0s, then `"Updated Xs ago"` or `"Updated Xm Ys ago"` for ≥ 60s

## Outcome

- ✅ Impact: Email loop has 3 independent guards now (own-address, Re: chain depth, message-id dedup). get_ticket silently-broken 404 fixed (store_id vs brand_id). Dashboard counter is live.
- 🧪 Tests: Manual — see test checklist above
- 📁 Files: 6 files modified, 1 migration created
- 🔁 Next prompts: Run migration 020 in Supabase; verify gmail_message_id stored on new tickets; deploy and check poller logs for "Skipping already-processed" on 2nd poll cycle
- 🧠 Reflection: The store_id/brand_id mismatch was a silent security regression — the tenant isolation check in get_ticket was effectively disabled because `ticket.get("brand_id")` always returned None for the vast majority of tickets. The auto-heal restores access for any brand that slipped through startup_heal.

## Evaluation notes (flywheel)

- Failure modes observed: if migration 020 hasn't been run yet, the gmail_message_id dedup query will hit a missing-column error and be silently caught (the `except: pass` guard keeps polling alive)
- Graders run and results (PASS/FAIL): none automated
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): add gmail_message_id to the thread-append path in email_poller so we skip even when appending to an existing ticket
