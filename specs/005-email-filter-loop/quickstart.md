# Quickstart: Email Filtering & Loop Prevention

**Feature**: 005-email-filter-loop  
**Date**: 2026-05-15

This document describes the integration scenarios for verifying the email filter pipeline end-to-end. Each scenario can be tested manually using a real Gmail inbox connected to Resolv, or via unit tests against `email_filter_service.py` with fixture message objects.

---

## Prerequisites

1. Docker stack running: `docker compose up backend email_poller`
2. Migration applied: `backend/migrations/011_email_filter_schema.sql`
3. A brand with Gmail connected (OAuth token stored in `brands.gmail_token`)
4. A valid tenant v1 JWT for the Settings API calls

---

## Scenario 1: Promotional Email Is Silently Discarded

**Tests**: FR-002, FR-003, FR-004, FR-006 | US1

**Setup**: Send an email from `newsletter@klaviyo-mail.io` with subject "50% off — this weekend only!" and body containing "unsubscribe" and "limited offer".

**Steps**:
1. Email arrives in the Gmail inbox
2. Email poller runs (≤ 60 s)
3. `email_filter_service.evaluate()` is called
4. Filter matches `blocked_sender_pattern` (prefix `newsletter@`) — returns `decision: blocked`
5. Filter logs the decision to `email_filter_log`
6. No ticket is created; no AI reply is sent

**Verify**:
```sql
SELECT decision, filter_reason, sender_email
FROM email_filter_log
WHERE sender_email = 'newsletter@klaviyo-mail.io'
ORDER BY created_at DESC LIMIT 1;
-- Expected: decision='blocked', filter_reason='blocked_sender_pattern'
```
```sql
SELECT COUNT(*) FROM tickets WHERE store_id = '<brand_id>'
  AND created_at > NOW() - INTERVAL '5 minutes';
-- Expected: 0
```

---

## Scenario 2: Auto-Reply Header Is Detected and Email Is Blocked

**Tests**: FR-005 | US1

**Setup**: Send an email with headers `Auto-Submitted: auto-replied` and `Precedence: bulk`.

**Steps**:
1. Poller fetches the email and extracts headers from the Gmail message payload
2. Filter checks `Auto-Submitted` header value → matches `auto-generated|auto-replied`
3. Returns `decision: blocked, reason: auto_reply_header`

**Verify**:
```sql
SELECT filter_reason FROM email_filter_log
WHERE filter_reason = 'auto_reply_header'
ORDER BY created_at DESC LIMIT 1;
-- Expected: 1 row
```

---

## Scenario 3: Gmail Category Label Blocks Promotional Email

**Tests**: FR-002 | US1

**Setup**: Gmail classifies an incoming email as `CATEGORY_PROMOTIONS` (labelIds includes it).

**Steps**:
1. Poller fetches message; `labelIds` contains `CATEGORY_PROMOTIONS`
2. Filter returns `decision: blocked, reason: gmail_category, email_category: promotional`

**Verify**:
```sql
SELECT email_category, filter_reason FROM email_filter_log
ORDER BY created_at DESC LIMIT 1;
-- Expected: email_category='promotional', filter_reason='gmail_category'
```

---

## Scenario 4: Real Customer Support Email Passes All Filters

**Tests**: FR-001, FR-007 | US3

**Setup**: Send from `customer@gmail.com`, subject "My order hasn't arrived", body "Hi, I ordered last week and the package is missing. Can you help?"

**Steps**:
1. Not on blocklist; no blocked prefix; not Gmail promotional category
2. No auto-reply headers
3. No promotional keywords in body
4. Filter returns `decision: allowed, email_category: support, sender_type: human`
5. Ticket is created with `email_category='support'`, `sender_type='human'`
6. Message processor generates AI reply

**Verify**:
```sql
SELECT email_category, sender_type, loop_risk, auto_reply_count
FROM tickets WHERE store_id = '<brand_id>'
ORDER BY created_at DESC LIMIT 1;
-- Expected: email_category='support', sender_type='human', loop_risk=false, auto_reply_count=0
```
```sql
SELECT decision FROM email_filter_log ORDER BY created_at DESC LIMIT 1;
-- Expected: decision='allowed'
```

---

## Scenario 5: AI Reply Loop Is Detected and Stopped

**Tests**: FR-008, FR-009, FR-010, FR-011 | US2

**Setup**: Thread with `gmail_thread_id='thread_abc123'` already has `auto_reply_count=2` (default `max_auto_replies=2`).

**Steps**:
1. New message arrives in thread `thread_abc123`
2. Poller fetches the thread's ticket: `auto_reply_count=2`, `max_auto_replies=2`
3. `check_loop_risk()` returns `True`
4. Ticket is updated: `loop_risk=true`
5. Filter logs `decision: blocked, reason: loop_risk`
6. No AI reply is generated

**Verify**:
```sql
SELECT loop_risk, auto_reply_count FROM tickets
WHERE gmail_thread_id = 'thread_abc123';
-- Expected: loop_risk=true, auto_reply_count=2
```
```sql
SELECT filter_reason FROM email_filter_log
WHERE filter_reason = 'loop_risk' ORDER BY created_at DESC LIMIT 1;
-- Expected: 1 row
```

**Reset**:
```bash
curl -X PATCH http://localhost:8000/api/tickets/<ticket_id> \
  -H "Authorization: Bearer <v1_token>" \
  -d '{"loop_risk": false}'
```

---

## Scenario 6: Whitelisted Domain Bypasses Sender-Pattern Filter

**Tests**: FR-013 | US4

**Setup**:
1. Add `trusteddomain.com` to whitelist via API:
```bash
curl -X PATCH http://localhost:8000/api/v1/settings/email-filter \
  -H "Authorization: Bearer <v1_token>" \
  -H "Content-Type: application/json" \
  -d '{"whitelisted_domains": ["trusteddomain.com"]}'
```
2. Send email from `noreply@trusteddomain.com`

**Steps**:
1. Filter loads settings: `whitelisted_domains=['trusteddomain.com']`
2. Sender domain `trusteddomain.com` matches whitelist
3. Sender-pattern check (layer 3) is skipped
4. Gmail category and content checks still apply
5. If no other disqualifying signals: `decision: allowed`

**Verify**:
```sql
SELECT decision FROM email_filter_log
WHERE sender_email = 'noreply@trusteddomain.com'
ORDER BY created_at DESC LIMIT 1;
-- Expected: decision='allowed'
```

---

## Scenario 7: Dashboard Widget Shows Correct Filter Counts

**Tests**: FR-018, FR-019 | US5

**Setup**: At least 10 emails filtered across multiple reasons in the last 7 days.

**Steps**:
1. Call the filter log summary endpoint:
```bash
curl http://localhost:8000/api/v1/filter-logs?summary=true&days=7 \
  -H "Authorization: Bearer <v1_token>"
```
2. Response contains:
```json
{
  "total_blocked": 10,
  "by_reason": {
    "gmail_category": 4,
    "blocked_sender_pattern": 3,
    "auto_reply_header": 2,
    "loop_risk": 1
  },
  "prevented_loops": 1
}
```
3. Dashboard widget renders correctly with these values

---

## Scenario 8: Filter Settings API Round-Trip

**Tests**: FR-013–FR-017 | US4

```bash
# GET current settings
curl http://localhost:8000/api/v1/settings/email-filter \
  -H "Authorization: Bearer <v1_token>"

# Expected:
# {
#   "blocked_domains": [],
#   "whitelisted_domains": [],
#   "max_auto_replies": 2,
#   "promotion_filter_enabled": true,
#   "loop_protection_enabled": true
# }

# PATCH to update
curl -X PATCH http://localhost:8000/api/v1/settings/email-filter \
  -H "Authorization: Bearer <v1_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "blocked_domains": ["spamco.io"],
    "max_auto_replies": 1,
    "promotion_filter_enabled": false
  }'

# GET again to verify persistence
curl http://localhost:8000/api/v1/settings/email-filter \
  -H "Authorization: Bearer <v1_token>"
# Expected: blocked_domains=["spamco.io"], max_auto_replies=1, promotion_filter_enabled=false
```

---

## Edge Case Scenarios

### EC-1: No Gmail Category Labels Available

Email fetched without `CATEGORY_*` labels in `labelIds` (permission gap or label absent).

**Expected**: Filter skips Gmail category check, continues to sender-pattern and content checks. No crash.

### EC-2: Email on Both Whitelist and Blocklist

Sender domain is in both `whitelisted_domains` and `blocked_domains`.

**Expected**: Whitelist takes priority — email passes domain checks (per spec edge case rule).

### EC-3: `max_auto_replies` = 0

Setting configured to `0`.

**Expected**: Every AI reply opportunity is blocked; all replies require human approval. `auto_reply_count` is still tracked (for visibility) but loop threshold is effectively immediate.

### EC-4: Thread Has No `gmail_thread_id`

Ticket exists but `gmail_thread_id` is null.

**Expected**: Loop detection skipped for this thread. All other filters still apply.

### EC-5: `Auto-Submitted: no` Header

Email explicitly declares it is not automated.

**Expected**: Header check honours the `no` value — email is NOT blocked by the auto-reply header filter.
