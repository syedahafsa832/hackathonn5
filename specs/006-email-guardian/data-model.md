# Data Model: Email Guardian

**Feature**: 006-email-guardian
**Date**: 2026-05-15

---

## New Table: `email_quarantine`

Holds emails that passed Layers 1–3 but were classified as `customer_support` with confidence below the brand's threshold. Operators promote or discard these.

```sql
CREATE TABLE IF NOT EXISTS email_quarantine (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        UUID        NOT NULL,
    sender_email    TEXT        NOT NULL,
    subject         TEXT,
    body_preview    TEXT,                       -- first 500 chars of body
    thread_id       TEXT,                       -- Gmail thread_id if available
    ai_classification TEXT,                    -- customer_support | unknown
    ai_confidence   FLOAT,                     -- 0.0–1.0
    status          TEXT        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending','promoted','discarded','expired')),
    actioned_by     TEXT,                       -- operator email who actioned it
    actioned_at     TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '7 days'),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_quarantine_brand_status
    ON email_quarantine (brand_id, status, created_at DESC);
```

### State Transitions

```
pending → promoted   (operator clicks "Promote to Ticket")
pending → discarded  (operator clicks "Discard")
pending → expired    (expires_at < now(), lazy cleanup on list query)
```

---

## Modified Table: `email_filter_log`

Two new nullable columns for AI classification audit. NULL when the guardian never ran (email blocked by layers 1–3).

```sql
ALTER TABLE email_filter_log
    ADD COLUMN IF NOT EXISTS ai_classification TEXT,    -- classification label
    ADD COLUMN IF NOT EXISTS ai_confidence     FLOAT;   -- confidence score 0.0–1.0
```

New `filter_reason` values used by the guardian:
- `"ai_classification"` — email blocked because classification ≠ `customer_support` and `support_only_mode=true`
- `"low_confidence"` — email quarantined because `confidence < confidence_threshold`

---

## Modified Table: `system_settings`

Three new columns for guardian configuration. All have safe production defaults.

```sql
ALTER TABLE system_settings
    ADD COLUMN IF NOT EXISTS support_only_mode    BOOLEAN DEFAULT true,
    ADD COLUMN IF NOT EXISTS confidence_threshold FLOAT   DEFAULT 0.75,
    ADD COLUMN IF NOT EXISTS auto_reply_enabled   BOOLEAN DEFAULT true;
```

### Settings Semantics

| Column | Type | Default | Meaning |
|--------|------|---------|---------|
| `support_only_mode` | bool | `true` | When true, only `customer_support` emails create tickets |
| `confidence_threshold` | float | `0.75` | Minimum AI confidence to allow ticket creation; below → quarantine |
| `auto_reply_enabled` | bool | `true` | When false, tickets are created but no AI reply is sent |

---

## Existing Tables (unchanged)

- `tickets` — no new columns; guardian sets `email_category='support'` and `sender_type='human'` on promoted quarantine records (same fields added in feature 005)
- `brands` — unchanged
- `customers` — unchanged
- `email_filter_log` — extended (see above)

---

## Entity Relationships

```
system_settings (1) ─── configures ──→ brand email pipeline
                                              │
                                    email_filter_service (Layers 1-3)
                                              │ allowed
                                    email_guardian_service (Layers 4-5)
                                         /         \
                                    blocked       quarantined
                                        │               │
                                 email_filter_log  email_quarantine
                                                        │ promote
                                                      tickets
```

---

## Migration File

`backend/migrations/012_email_guardian_schema.sql`

```sql
BEGIN;

-- 1. Extend email_filter_log with AI classification audit columns
ALTER TABLE email_filter_log
    ADD COLUMN IF NOT EXISTS ai_classification TEXT,
    ADD COLUMN IF NOT EXISTS ai_confidence     FLOAT;

-- 2. Add guardian settings to system_settings
ALTER TABLE system_settings
    ADD COLUMN IF NOT EXISTS support_only_mode    BOOLEAN DEFAULT true,
    ADD COLUMN IF NOT EXISTS confidence_threshold FLOAT   DEFAULT 0.75,
    ADD COLUMN IF NOT EXISTS auto_reply_enabled   BOOLEAN DEFAULT true;

-- 3. Quarantine queue
CREATE TABLE IF NOT EXISTS email_quarantine (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        UUID        NOT NULL,
    sender_email    TEXT        NOT NULL,
    subject         TEXT,
    body_preview    TEXT,
    thread_id       TEXT,
    ai_classification TEXT,
    ai_confidence   FLOAT,
    status          TEXT        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending','promoted','discarded','expired')),
    actioned_by     TEXT,
    actioned_at     TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '7 days'),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_quarantine_brand_status
    ON email_quarantine (brand_id, status, created_at DESC);

COMMIT;
```
