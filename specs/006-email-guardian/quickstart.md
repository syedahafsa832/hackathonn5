# Quickstart: Email Guardian Integration Test Scenarios

**Feature**: 006-email-guardian
**Date**: 2026-05-15
**Prerequisites**: Migration `012_email_guardian_schema.sql` applied; Docker stack running

---

## Setup

```bash
# Apply migration in Supabase SQL editor, then:
docker compose restart backend email_poller
```

Verify new columns exist:
```sql
SELECT support_only_mode, confidence_threshold, auto_reply_enabled
FROM system_settings LIMIT 1;
-- Expected: true, 0.75, true (defaults)

SELECT id FROM email_quarantine LIMIT 1;
-- Expected: empty table (no error)
```

---

## Scenario 1 — Sales Pitch Passes Layers 1–3 but AI Blocks It

**Setup**: Send an email from a plain Gmail address (not blocklisted) with subject "Quick question about your product" and body: "Hi, I wanted to reach out about a partnership opportunity. We offer growth hacking tools that could 10x your revenue. Click here to schedule a call."

**Expected**:
- No ticket created
- `email_filter_log` row: `filter_reason='ai_classification'`, `ai_classification='outreach'`, `ai_confidence >= 0.7`
- `email_quarantine` — no row (blocked, not quarantined)

```sql
SELECT filter_reason, ai_classification, ai_confidence
FROM email_filter_log
WHERE sender_email = 'sender@example.com'
ORDER BY created_at DESC LIMIT 1;
```

---

## Scenario 2 — Genuine Support Email Passes All 5 Layers

**Setup**: Send from a plain Gmail address with subject "Order #4521 hasn't arrived" and body: "Hi, I placed an order 10 days ago and it still hasn't arrived. Can you help me track it? My order number is 4521."

**Expected**:
- Ticket created with `email_category='support'`, `sender_type='human'`
- `email_filter_log` row: `decision='allowed'`, `ai_classification='customer_support'`, `ai_confidence >= 0.75`
- No quarantine row

```sql
SELECT ai_classification, ai_confidence, decision
FROM email_filter_log
ORDER BY created_at DESC LIMIT 1;
-- Expected: customer_support, >=0.75, allowed
```

---

## Scenario 3 — Low-Confidence Email Goes to Quarantine

**Setup**: Set `confidence_threshold=0.90` via PATCH, then send an email that the classifier scores around 0.75 (e.g., ambiguous wording: "I have a problem with something I ordered, can you check?").

```bash
curl -X PATCH http://localhost:8001/api/v1/settings/email-filter \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"confidence_threshold": 0.90}'
```

**Expected**:
- No ticket created
- `email_filter_log` row: `filter_reason='low_confidence'`, `decision='quarantined'`
- `email_quarantine` row: `status='pending'`, `ai_confidence ~ 0.75`

```sql
SELECT id, ai_confidence, status FROM email_quarantine
WHERE status='pending' ORDER BY created_at DESC LIMIT 1;
```

---

## Scenario 4 — Operator Promotes Quarantined Email to Ticket

**Setup**: A quarantine record exists from Scenario 3.

```bash
# Get quarantine ID
QUID=$(curl -s http://localhost:8001/api/v1/quarantine \
  -H "Authorization: Bearer $TOKEN" | jq -r '.items[0].id')

# Promote
curl -X POST http://localhost:8001/api/v1/quarantine/$QUID/promote \
  -H "Authorization: Bearer $TOKEN"
```

**Expected**:
- Response: `{"success": true, "ticket_id": "<uuid>"}`
- `email_quarantine.status` updated to `promoted`
- Ticket exists in `tickets` table with `email_category='support'`

```sql
SELECT status, actioned_at FROM email_quarantine WHERE id = '<QUID>';
-- Expected: promoted, <timestamp>
```

---

## Scenario 5 — Operator Discards Quarantined Email

```bash
QUID2=$(curl -s "http://localhost:8001/api/v1/quarantine?status=pending" \
  -H "Authorization: Bearer $TOKEN" | jq -r '.items[0].id')

curl -X POST http://localhost:8001/api/v1/quarantine/$QUID2/discard \
  -H "Authorization: Bearer $TOKEN"
```

**Expected**:
- `email_quarantine.status = 'discarded'`
- No ticket created

---

## Scenario 6 — Support-Only Mode Blocks Non-Support with `support_only_mode=true`

**Setup**: Confirm defaults (`support_only_mode=true`). Send a cold email: "I'd like to discuss a collaboration."

**Expected**: Blocked. `filter_reason='ai_classification'`, classification = `outreach`.

---

## Scenario 7 — Support-Only Mode Disabled, Non-Support Proceeds

```bash
curl -X PATCH http://localhost:8001/api/v1/settings/email-filter \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"support_only_mode": false}'
```

Send the same cold email as Scenario 6.

**Expected**: AI classification runs and is logged, but does NOT block the email. Ticket created. (Layers 1–3 still apply — only the classification gate is skipped.)

---

## Scenario 8 — `auto_reply_enabled=false` Creates Ticket Without AI Reply

```bash
curl -X PATCH http://localhost:8001/api/v1/settings/email-filter \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"auto_reply_enabled": false}'
```

Send a genuine support email.

**Expected**:
- Ticket created
- No AI-generated email reply sent
- `tickets.auto_reply_count = 0`

---

## Scenario 9 — Classifier Unavailable Quarantines Email (Fail Open)

**Setup**: Temporarily break the Mistral API key (`MISTRAL_API_KEY=invalid`), restart email_poller, send a support email.

**Expected**:
- Email poller logs a WARNING about classifier failure
- Email quarantined with `ai_classification='unknown'`, `ai_confidence=0.0`
- No ticket created when `support_only_mode=true`
- No crash

```sql
SELECT ai_classification, ai_confidence, filter_reason
FROM email_filter_log ORDER BY created_at DESC LIMIT 1;
-- Expected: unknown, 0.0, low_confidence
```

---

## Scenario 10 — Dashboard Widget Shows Quarantine Count

Open ai-ops-console dashboard.

**Expected**:
- "Filtered Emails" widget shows `quarantined: N` (matching `email_quarantine` count)
- `by_reason` includes `ai_classification` and `low_confidence` counts
- "Review Quarantine" link is clickable when N > 0

---

## Scenario 11 — New Brand Inherits Safe Defaults

Create a new brand (no custom system_settings row).

```bash
curl http://localhost:8001/api/v1/settings/email-filter \
  -H "Authorization: Bearer $NEW_BRAND_TOKEN"
```

**Expected**:
```json
{
  "support_only_mode": true,
  "confidence_threshold": 0.75,
  "auto_reply_enabled": true,
  "max_auto_replies": 2,
  "promotion_filter_enabled": true,
  "loop_protection_enabled": true,
  "blocked_domains": [],
  "whitelisted_domains": []
}
```
