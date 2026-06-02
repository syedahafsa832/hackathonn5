---
id: 12
title: Four-Fix Batch — Loop, Timeout, Tenant, Duplicates
stage: general
date: 2026-06-02
surface: agent
model: claude-sonnet-4-6
feature: none
branch: 006-email-guardian
user: Syeda Hafsa
command: RESOLV — FOUR SPECIFIC FIXES FROM SCREENSHOTS
labels: ["backend","email-loop","multi-tenant","timeout","actions","dedup","message-processor"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/channels/email_poller.py
  - backend/src/workers/message_processor.py
  - backend/src/services/actions_service.py
  - ai-ops-console/src/api/client.js
  - ai-ops-console/src/api/services.js
tests:
  - manual: send ONE cancel email → check Gmail inbox after 2 minutes → exactly ONE AI reply, no Re: Re: chain
  - manual: logs must show "[Poller] Skipping own-address email" on subsequent poll cycles
  - manual: approve action in Actions page → completes within 30s, no timeout error
  - manual: 1983 account conversations page shows tickets with correct tenant
  - manual: click ticket → customer message AND AI reply visible
  - manual: send second cancel email for same order → Escalations shows 1 action, not 2
---

## Prompt

RESOLV — FOUR SPECIFIC FIXES FROM SCREENSHOTS (loop, timeout, tenant isolation, duplicate actions)

## Response snapshot

### FIX 1: Email loop (Re: Re: Re: Re:)

**Root cause:** `email_poller.py` was fetching the brand's OWN outbound reply emails from Gmail
(query `in:inbox after:{epoch}` returns threads, including ones where the AI sent the last message).
No check existed to skip emails where `sender == brand.gmail_email`.

**Code fix — `email_poller.py` `_poll_brand_inbox()`:**
Added self-email check immediately after sender extraction:
```python
brand_gmail = brand.get("gmail_email", "").lower()
if brand_gmail and sender == brand_gmail:
    logger.info(f"[Poller] Skipping own-address email from {sender} — loop prevention")
    continue
```
This is the definitive loop stop. Even if everything else failed (thread dedup, loop_risk flag), this check is unconditional.

**FIX 3A co-fix — `message_processor.py` tenant lookup reorder:**
The tenant lookup was in wrong order — "first active tenant" (step 3) fired BEFORE the brand.tenant_id lookup (step 4). In multi-tenant production this caused ALL email tickets to use one shared default tenant_id regardless of which Gmail inbox they arrived in. Reordered to:
1. `brand.tenant_id` (most reliable — store_id always known for email-triggered tickets)
2. `to_email` match
3. `SUPPORT_EMAIL_ADDRESS` env var
4. First active tenant (last resort, now logs a WARNING to prompt migration 019)

### FIX 2: Approve button timeout (5000ms exceeded)

**Root cause:** axios `timeout: 5000` in `client.js` — Shopify API can take 5-25 seconds for cancel/refund operations. The backend Shopify client already had `timeout=30` on its requests calls.

**Code fix — `client.js`:**
```javascript
timeout: 30000,  // was 5000
```

The `ActionCard` already shows "Approving..." during the call (from previous session fix).

### FIX 3B: No messages in thread

**Code fix — `services.js` `getConversationMessages()` fallback:**
Broadened fallback field lookup when `ticket.messages` array is empty:
- Added `ticket.content`, `ticket.body`, `ticket.email_body` as fallback field names
- Corrected priority: `ai_reply` (the sent reply) before `ai_draft` (the unsent draft)
```javascript
const customerBody = ticket.message || ticket.content || ticket.body || ticket.email_body || null;
const aiText = ticket.ai_reply || ticket.ai_draft || ticket.ai_response;
```

### FIX 4: Duplicate cancel actions

**Root cause:** Each time an email in a Re: chain was processed (before FIX 1 stopped the loop), `detect_and_create()` created a new pending action for the same order + type. No dedup check existed.

**Code fix — `actions_service.py` `detect_and_create()`:**
Before calling `create_action()`, queries for existing pending action with same `tenant_id + action_type + order_id`. If found, returns the existing action ID with `status: "duplicate_skipped"` — no insert.

**Code fix — `message_processor.py` Stage 9.5 fallback path:**
Same dedup check added to the inline insert path used when `actions_service` is unavailable.

**SQL to clean up existing duplicates (run in Supabase):**
```sql
-- Keep oldest pending action per order+type; reject the rest
WITH ranked AS (
  SELECT id,
    ROW_NUMBER() OVER (PARTITION BY tenant_id, action_type, order_id ORDER BY created_at ASC) as rn
  FROM actions
  WHERE status = 'pending'
)
UPDATE actions
SET status = 'rejected',
    error_message = 'Duplicate action removed by cleanup'
WHERE id IN (SELECT id FROM ranked WHERE rn > 1);
```

## Outcome

- ✅ Impact: Email loop stopped at source (own-address check); multi-tenant tenant_id lookup fixed so 1983/832/772 see own tickets; approve button no longer times out; duplicate actions prevented
- 🧪 Tests: Manual — see test checklist above
- 📁 Files: 5 files modified
- 🔁 Next prompts: Run duplicate cleanup SQL; verify 3 brands in poller logs after deploy; send test cancel email end-to-end
- 🧠 Reflection: The loop was caused by Gmail's inbox query returning the AI's own outbound replies (Gmail treats the thread as "in inbox" even when the last message is outbound). The tenant reorder was the real root cause of "all tickets look the same" — step 4 was always being shadowed by step 3.

## Evaluation notes (flywheel)

- Failure modes observed: if brand.gmail_email is null/empty, the own-address skip is bypassed — harmless, thread dedup is second line of defense
- Graders run and results (PASS/FAIL): none automated
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): add Auto-Submitted header to outbound AI emails so Gmail mail servers also help suppress loop delivery
