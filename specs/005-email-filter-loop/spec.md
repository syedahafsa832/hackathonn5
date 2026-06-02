# Feature Specification: Email Filtering & Loop Prevention System

**Feature Branch**: `005-email-filter-loop`
**Created**: 2026-05-15
**Status**: Draft

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Automated & Promotional Emails Are Silently Discarded (Priority: P1)

A brand owner has connected their Gmail inbox to Resolv. They receive newsletters, LinkedIn notifications, SaaS trial emails, and marketing blasts every day. None of these should ever create support tickets or trigger AI replies. The system must silently discard them before any downstream processing occurs.

**Why this priority**: This is the core safety concern — unfiltered inboxes cause AI reply loops, reputation damage, and noise in the dashboard. Everything else depends on reliable filtering working first.

**Independent Test**: Send 10 promotional/newsletter emails to the connected inbox. Verify zero tickets are created, zero AI replies are sent, and each email appears in a filtered log with its reason.

**Acceptance Scenarios**:

1. **Given** a Gmail inbox receiving a newsletter with a `List-Unsubscribe` header, **When** the email poller fetches it, **Then** no ticket is created and the email is recorded in the filter log as `reason: auto_reply_header`.
2. **Given** an email from `noreply@linkedin.com`, **When** ingested, **Then** it is discarded with `reason: blocked_sender_pattern` and no AI response is sent.
3. **Given** an email whose body contains "unsubscribe", "webinar", and a discount offer, **When** classified, **Then** it is rejected as `email_type: promotional` and no ticket is created.
4. **Given** an email in Gmail category "promotions" or "social" or "updates", **When** fetched, **Then** it is discarded without ticket creation.

---

### User Story 2 — AI Reply Loop Is Detected and Stopped (Priority: P1)

A customer support AI replies to an email. The recipient also has an AI auto-responder and replies back immediately. Without protection, both systems reply indefinitely. The system must detect this pattern and stop replying after a configurable number of automated exchanges per thread.

**Why this priority**: A single unchecked loop produced ~20 unnecessary replies overnight. This is a production-safety requirement on par with the filtering itself.

**Independent Test**: Simulate a thread where the same sender replies with automated content twice. Verify that after the second exchange the thread is marked `loop_risk: true` and the AI stops replying, with an incident recorded in the logs.

**Acceptance Scenarios**:

1. **Given** a Gmail thread where 2 AI replies have already been sent, **When** a new message arrives in that thread, **Then** the system does NOT generate an AI reply and marks the ticket `loop_risk: true`.
2. **Given** a thread marked `loop_risk: true`, **When** a human agent manually resets the flag via the ticket UI, **Then** AI may resume on the next incoming message.
3. **Given** a sender who replies with automated header indicators (`Auto-Submitted`, `X-Autoreply`) in the same thread, **When** ingested, **Then** the message is discarded immediately without incrementing the AI reply counter.

---

### User Story 3 — Only Genuine Customer Support Emails Reach the Ticket Queue (Priority: P1)

A real customer writes "My order #1234 hasn't arrived, please help." This email must pass all filters and land in the ticket queue for AI processing. At the same time, a recruiting email from the same hour must be discarded. Intent detection must distinguish the two reliably.

**Why this priority**: Filtering must not over-block — missing a real customer is as damaging as replying to a bot.

**Independent Test**: Send 5 real support emails (refund request, shipping issue, damaged product, payment problem, address change) and 5 non-support emails. Verify all 5 support emails create tickets and all 5 non-support emails are discarded.

**Acceptance Scenarios**:

1. **Given** an email with subject "Where is my order?" from a real human address, **When** classified, **Then** a ticket is created with `sender_type: human` and `email_category: support`.
2. **Given** an email containing a refund request for a named product, **When** processed, **Then** it passes all filters and reaches AI analysis.
3. **Given** an email about a job opportunity or sponsorship, **When** classified, **Then** it is filtered as `email_type: promotional` and no ticket is created.

---

### User Story 4 — Brand Owner Configures Filter Rules from Settings (Priority: P2)

The brand owner opens the Settings page and can add domains to a block list, add domains to an allow list (whitelist), set the maximum automated replies per thread, and toggle promotion filtering and loop protection on or off per brand.

**Why this priority**: Default rules work for most inboxes, but brands have unique relationships (e.g., their own CRM sends legitimate automated emails). Configurability prevents false positives.

**Independent Test**: Add `trusteddomain.com` to the whitelist. Send an email from `noreply@trusteddomain.com`. Verify a ticket is created despite the `noreply@` prefix.

**Acceptance Scenarios**:

1. **Given** a domain added to the whitelist, **When** an email from that domain arrives, **Then** sender-pattern filters are bypassed and the email is eligible for ticket creation.
2. **Given** a domain added to the block list, **When** an email from that domain arrives, **Then** it is immediately discarded regardless of content.
3. **Given** max auto-replies set to 1, **When** a thread's second AI reply opportunity occurs, **Then** it is suppressed and the thread is flagged `loop_risk: true`.
4. **Given** promotion filtering toggled off, **When** a promotional email arrives, **Then** it is not filtered on content alone (though header and sender-pattern checks still apply).

---

### User Story 5 — Filtered Email Activity Is Visible in Dashboard (Priority: P3)

The brand owner can see a dashboard widget showing how many emails were filtered this week, a breakdown by reason (newsletter, auto-reply headers, promotional content, loop prevention), and a count of prevented AI loops.

**Why this priority**: Visibility gives operators confidence the system is working and helps tune filter rules over time.

**Independent Test**: After 20 filtered emails arrive, open the dashboard and verify the widget shows the correct counts broken down by filter reason.

**Acceptance Scenarios**:

1. **Given** 10 emails filtered in the past 24 hours, **When** the dashboard loads, **Then** the Filtered Emails widget shows 10 with a per-reason breakdown.
2. **Given** 2 loop-prevention events, **When** viewing the widget, **Then** the "Prevented AI Loops" count shows 2.

---

### Edge Cases

- What happens when an email has no headers at all (malformed)? Apply content-only checks; do not create a ticket if classification is ambiguous.
- What if the same sender is on both whitelist and blocklist? Whitelist takes priority.
- What if Gmail category labels are unavailable (API change or permission gap)? Fall back gracefully to sender-pattern and content checks; do not crash the pipeline.
- What if a real customer's email client sets `Auto-Submitted: no`? Honour the explicit `no` value — only block `auto-generated` and `auto-replied` values.
- What if a thread has no `gmail_thread_id`? Loop detection is skipped for that thread; all other filters still apply.
- What if `max_auto_replies` is set to 0? Treat as "never auto-reply" — all AI replies require human approval before sending.
- What if content filtering produces a false positive for a genuine customer query that happens to mention a webinar they attended? The whitelist per-sender or per-domain provides the escape hatch.

## Requirements *(mandatory)*

### Functional Requirements

**Pre-Ticket Filtering**

- **FR-001**: The system MUST evaluate every incoming email against all active filter rules before creating a ticket or triggering an AI reply.
- **FR-002**: The system MUST discard emails classified by Gmail as category `promotions`, `social`, or `updates` unless the sender domain is on the tenant whitelist.
- **FR-003**: The system MUST discard emails whose sender address matches blocked prefix patterns (`noreply@`, `no-reply@`, `notifications@`, `newsletter@`, `digest@`, `mailer@`, `hello@`, `marketing@`, `updates@`) unless the sender domain is whitelisted.
- **FR-004**: The system MUST discard emails whose sender domain matches any entry in the tenant block list or the built-in automated-domain list.
- **FR-005**: The system MUST discard emails containing auto-reply headers with values indicating automation: `Auto-Submitted` (values `auto-generated` or `auto-replied`), `Precedence: bulk`, `Precedence: list`, `X-Autoreply`, `X-Autorespond`, `List-Unsubscribe`.
- **FR-006**: The system MUST classify email body content and discard emails whose content indicates non-support intent based on promotional keyword signals (unsubscribe, discount, webinar, newsletter, outreach, sponsorship, recruiting, job alert, growth hacks, course offer, marketing call-to-action), when promotion filtering is enabled.
- **FR-007**: The system MUST record every evaluated email in a persistent filter log with: timestamp, sender address, brand ID, filter decision (allowed or blocked), and filter reason.

**AI Reply Loop Prevention**

- **FR-008**: The system MUST track the count of AI-generated replies sent per Gmail thread in the `auto_reply_count` field on the ticket.
- **FR-009**: The system MUST stop sending AI replies to a thread once `auto_reply_count` reaches the tenant's configured `max_auto_replies` threshold (default: 2).
- **FR-010**: When the reply threshold is reached, the system MUST mark the ticket `loop_risk: true` and record a loop-detection incident in the logs.
- **FR-011**: The system MUST NOT generate AI replies for any message in a thread already marked `loop_risk: true` until a human agent explicitly resets the flag.
- **FR-012**: The system MUST identify and discard auto-reply responses from the recipient (detected via headers) without counting them toward the human-reply total or triggering AI response generation.

**Admin Configuration**

- **FR-013**: Brand owners MUST be able to add and remove domains from a per-tenant whitelist via the Settings page.
- **FR-014**: Brand owners MUST be able to add and remove domains from a per-tenant block list via the Settings page.
- **FR-015**: Brand owners MUST be able to set the maximum auto-replies per thread (integer 0–10) via the Settings page.
- **FR-016**: Brand owners MUST be able to toggle promotion-content filtering on or off per brand.
- **FR-017**: Brand owners MUST be able to toggle loop-protection on or off per brand.

**Dashboard Visibility**

- **FR-018**: The dashboard MUST display a "Filtered Emails" widget showing the total filtered count and a breakdown by reason for the last 7 days.
- **FR-019**: The widget MUST separately show a "Prevented Loops" count distinct from other filter categories.

### Key Entities

- **FilterDecision**: A log record for each evaluated email — sender address, thread ID, brand ID, timestamp, decision (allowed/blocked), filter reason code, email category, sender type detected.
- **EmailFilterSettings**: Per-tenant/brand configuration record — whitelisted domains (list), blocked domains (list), `max_auto_replies` (integer, default 2), `promotion_filter_enabled` (boolean, default true), `loop_protection_enabled` (boolean, default true).
- **Ticket** (extended): Gains four new fields — `email_category` (text: support, promotional, social, updates, unknown), `sender_type` (text: human, automated, unknown), `loop_risk` (boolean, default false), `auto_reply_count` (integer, default 0).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Zero promotional or automated emails create support tickets under normal inbox conditions (no whitelist exceptions active).
- **SC-002**: No AI reply loop exceeds 2 automated exchanges per thread when loop protection is enabled with the default threshold.
- **SC-003**: All genuine customer support emails (refund, shipping, order issue, product damage, address change, payment query) pass filters and reach the ticket queue — false-positive rate below 1%.
- **SC-004**: Every filter decision is logged within 1 second of email ingestion.
- **SC-005**: The dashboard Filtered Emails widget reflects accurate counts within 60 seconds of new filter events occurring.
- **SC-006**: Whitelist and blocklist changes take effect within one email poll cycle (60 seconds) without requiring a system restart.
- **SC-007**: AI reply loops are stopped by reply-count enforcement alone, even when the sender uses a legitimate-looking address with no auto-reply headers.

## Scope

### In Scope

- Pre-ticket email classification integrated into the email polling pipeline
- Gmail category label detection using existing Gmail API access
- Sender-pattern matching (blocked prefixes and domains, tenant whitelist/blocklist)
- Email header inspection for auto-reply signals
- Promotional content keyword heuristics
- Per-thread AI reply counter with configurable threshold and loop flagging
- Per-tenant filter settings (whitelist, blocklist, thresholds, toggles)
- Filter decision logging
- Dashboard widget for filtered email activity and loop prevention counts
- Settings UI for managing filter configuration

### Out of Scope

- Machine-learning-based email classification (heuristic keyword matching only for v1)
- Spam scoring or DKIM/SPF/DMARC validation
- Blocking senders at the Gmail level (in-app filtering only)
- WhatsApp or web-form channel filtering (email channel only for v1)
- Retroactive filtering of emails already converted to tickets

## Assumptions

- The Gmail API returns category labels for inbox messages; if absent the system falls back to sender-pattern and content checks without failing.
- Tenants have one active brand per account in this version; filter settings are brand-scoped.
- The `gmail_thread_id` field on tickets is reliably populated for email-channel tickets; threads without it skip loop detection.
- Auto-reply detection relies on headers only — no semantic analysis of reply body content.
- `max_auto_replies` defaults to 2; setting it to 0 disables auto-send entirely (all replies require human approval).
- Filter logs are retained for 30 days aligned with the existing data retention setting.
- The built-in blocked-domain list covers major automated platforms; tenants can extend it via their block list without touching source code.
