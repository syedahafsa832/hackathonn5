# Data Model: Resolv MVP Completion

**Branch**: `004-resolv-completion` | **Date**: 2026-05-13

> No schema changes are made. This document maps existing tables to the features being fixed.

---

## Existing Tables Used

### `tenants`
Used by v1 auth system.
```
id              UUID (PK)
email           TEXT
company_name    TEXT
password_hash   TEXT
is_active       BOOL
shopify_connected BOOL
created_at      TIMESTAMPTZ
last_login_at   TIMESTAMPTZ
```
**Auth fix (Item A)**: `auth_middleware` fallback reads this table when `sub` is a tenant_id.

---

### `tickets`
Core entity for items B, C, E, H.
```
id              UUID (PK)
store_id        UUID (FK → brands.id)
customer_email  TEXT           ← Item C: must be returned in list
channel         TEXT           ← Item C: default 'email' when null
subject         TEXT
message         TEXT
ai_draft        TEXT
ai_reply        TEXT
status          TEXT           ← 'open'|'resolved'|'escalated'
email_sent      BOOL           ← Item E: set true after send
gmail_thread_id TEXT           ← Item H: used for thread matching
created_at      TIMESTAMPTZ
updated_at      TIMESTAMPTZ
```

**Item C**: `channel` and `customer_email` returned in list endpoint.
**Item E**: `email_sent` and `status` updated after Gmail send.
**Item H**: `gmail_thread_id` used to match inbound replies.

---

### `actions` / `pending_actions`
Used by items B, E for stats and approval workflow.
```
id              UUID (PK)
tenant_id       UUID (FK → tenants.id)
action_type     TEXT           ← 'refund'|'cancel_order'|'change_address'
status          TEXT           ← 'pending'|'approved'|'rejected'
customer_email  TEXT
order_id        TEXT
confidence      FLOAT
created_at      TIMESTAMPTZ
```

**Item B**: Action count used in dashboard AI Handled % calculation.

---

### `brands`
Used by items E, G, H.
```
id              UUID (PK)
tenant_id       UUID (FK → tenants.id)
shopify_domain  TEXT
gmail_connected BOOL           ← Item E: must be true for email send
gmail_credentials JSONB
created_at      TIMESTAMPTZ
```

**Item G**: `brands` count checked on Dashboard mount for onboarding redirect.
**Item E**: `gmail_connected` checked before attempting send.

---

### `knowledge_base_sources`
Used by item F.
```
id              UUID (PK)
tenant_id       UUID (FK → tenants.id)
title           TEXT
content         TEXT
created_at      TIMESTAMPTZ
```

**Item F**: Listed, created, and deleted via Settings KB tab.

---

## State Transitions

### Ticket status (Item E)
```
open → (approve-ai clicked, email sent) → resolved
open → (brand owner escalates) → escalated
escalated → (brand owner resolves) → resolved
```

### Action status (confidence gating)
```
pending → (brand owner approves) → approved → (executor runs) → executed
pending → (brand owner rejects) → rejected
```
No financial action executes without transitioning through `approved`.

---

## No Schema Changes Required

All fields referenced by the 9 items already exist in the database schema.
The only "change" is ensuring backend routes return these fields and
frontend renders them.
