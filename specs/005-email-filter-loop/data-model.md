# Data Model: Email Filtering & Loop Prevention

**Feature**: 005-email-filter-loop
**Date**: 2026-05-15

---

## Schema Changes

### 1. New Table: `email_filter_log`

Append-only log of every email evaluated by the filter service. Used by the dashboard widget.

```sql
CREATE TABLE IF NOT EXISTS email_filter_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        UUID NOT NULL,
    sender_email    TEXT NOT NULL,
    thread_id       TEXT,                   -- gmail_thread_id, may be null
    decision        TEXT NOT NULL,          -- 'allowed' | 'blocked'
    filter_reason   TEXT,                   -- reason code when blocked (see below)
    email_category  TEXT,                   -- 'support' | 'promotional' | 'social' | 'updates' | 'unknown'
    sender_type     TEXT,                   -- 'human' | 'automated' | 'unknown'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_email_filter_log_brand_created ON email_filter_log (brand_id, created_at DESC);
```

**Filter reason codes** (stored in `filter_reason`):
| Code | Meaning |
|------|---------|
| `gmail_category` | Gmail classified as promotions / social / updates |
| `blocked_sender_pattern` | Sender prefix matched blocked list (noreply@, etc.) |
| `blocked_domain` | Sender domain on tenant block list or global list |
| `auto_reply_header` | Email contained auto-reply headers |
| `promotional_content` | Body keyword heuristics matched promotional pattern |
| `loop_risk` | Thread already at or over max_auto_replies |
| `self_reply` | Email is from the brand's own support address |

---

### 2. Extended Table: `system_settings`

New columns added via migration. All new columns are nullable with safe defaults so existing rows are unaffected.

```sql
ALTER TABLE system_settings
    ADD COLUMN IF NOT EXISTS blocked_domains        JSONB DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS whitelisted_domains    JSONB DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS max_auto_replies       INTEGER DEFAULT 2,
    ADD COLUMN IF NOT EXISTS promotion_filter_enabled BOOLEAN DEFAULT true,
    ADD COLUMN IF NOT EXISTS loop_protection_enabled  BOOLEAN DEFAULT true;
```

**Column semantics**:
- `blocked_domains`: JSON array of lowercase domain strings, e.g. `["spam.com", "newsletter.io"]`
- `whitelisted_domains`: JSON array of lowercase domain strings; these bypass sender-pattern and Gmail-category checks
- `max_auto_replies`: Integer 0–10; 0 means all AI replies require human approval
- `promotion_filter_enabled`: When false, keyword-content filter (FR-006) is skipped; header and sender checks remain active
- `loop_protection_enabled`: When false, `auto_reply_count` is still tracked but never blocks sends

---

### 3. Extended Table: `tickets`

New columns for per-ticket classification state.

```sql
ALTER TABLE tickets
    ADD COLUMN IF NOT EXISTS email_category   TEXT,
    ADD COLUMN IF NOT EXISTS sender_type      TEXT,
    ADD COLUMN IF NOT EXISTS loop_risk        BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS auto_reply_count INTEGER DEFAULT 0;
```

**Column semantics**:
- `email_category`: Filled on ticket creation — `'support'`, `'promotional'`, `'social'`, `'updates'`, `'unknown'`
- `sender_type`: `'human'`, `'automated'`, `'unknown'`
- `loop_risk`: Set to `true` when `auto_reply_count >= max_auto_replies`; must be manually reset to `false` by a human agent
- `auto_reply_count`: Incremented by 1 each time the message processor actually sends or queues an AI reply for this thread

---

## Entity Relationships

```
system_settings (store_id / brand_id)
  └── blocked_domains        []  per-brand block list
  └── whitelisted_domains    []  per-brand allow list
  └── max_auto_replies       int loop threshold
  └── promotion_filter_enabled
  └── loop_protection_enabled

email_filter_log (brand_id → brands.id)
  └── Records every filter decision (allowed or blocked)
  └── Used for dashboard widget aggregation

tickets (store_id → brands.id)
  └── email_category, sender_type  — set at creation
  └── loop_risk, auto_reply_count  — updated per AI reply
```

---

## State Transitions: `tickets.loop_risk`

```
false (initial)
  │
  │  [auto_reply_count incremented to max_auto_replies]
  ▼
true  ←──────────────────────────────────────────────────────
  │                                                          │
  │  [human agent resets via UI / API PATCH]                │
  ▼                                                          │
false  [resumes normal AI handling]          [if new reply   │
                                              arrives again] │
                                                             │
                                              true ──────────┘
```

---

## Migration File

Path: `backend/migrations/011_email_filter_schema.sql`

```sql
-- Migration 011: Email filter schema
-- Adds email_filter_log table and extends system_settings and tickets

BEGIN;

-- 1. New email filter log table
CREATE TABLE IF NOT EXISTS email_filter_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        UUID NOT NULL,
    sender_email    TEXT NOT NULL,
    thread_id       TEXT,
    decision        TEXT NOT NULL CHECK (decision IN ('allowed', 'blocked')),
    filter_reason   TEXT,
    email_category  TEXT,
    sender_type     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_filter_log_brand_created
    ON email_filter_log (brand_id, created_at DESC);

-- 2. Extend system_settings with filter config
ALTER TABLE system_settings
    ADD COLUMN IF NOT EXISTS blocked_domains          JSONB   DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS whitelisted_domains      JSONB   DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS max_auto_replies         INTEGER DEFAULT 2,
    ADD COLUMN IF NOT EXISTS promotion_filter_enabled BOOLEAN DEFAULT true,
    ADD COLUMN IF NOT EXISTS loop_protection_enabled  BOOLEAN DEFAULT true;

-- 3. Extend tickets with classification fields
ALTER TABLE tickets
    ADD COLUMN IF NOT EXISTS email_category   TEXT,
    ADD COLUMN IF NOT EXISTS sender_type      TEXT,
    ADD COLUMN IF NOT EXISTS loop_risk        BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS auto_reply_count INTEGER DEFAULT 0;

COMMIT;
```
