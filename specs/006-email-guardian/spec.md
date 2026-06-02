# Feature Specification: Email Guardian — AI-Powered Support-Only Email Pipeline

**Feature Branch**: `006-email-guardian`
**Created**: 2026-05-15
**Status**: Draft
**Input**: User description: "CRITICAL EMAIL SAFETY + CUSTOMER SUPPORT MODE REWORK"

## Context

This feature extends the rule-based filtering already in place (feature 005: domain blocklists, sender prefix matching, Gmail category labels, auto-reply headers, loop prevention) with two additional intelligence layers: an AI intent classifier and a confidence gate. It also introduces a **Support-Only Mode** toggle and a **quarantine queue** for emails that need human review before any action.

The existing `email_filter_service.py` handles Layers 1–3 and loop detection. This feature adds Layers 4–5 via a new `email_guardian_service.py` and the supporting data model, settings, and UI.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — AI Intent Classification Blocks Non-Support Emails (Priority: P1)

A brand owner connects their Gmail inbox. Over 24 hours the inbox receives a mix of real customer emails (order questions, refund requests) and non-support email (LinkedIn outreach, Klaviyo newsletters, Skool digests, cold sales pitches). The guardian classifies every incoming email before any ticket is created. Only emails classified as `customer_support` with sufficient confidence reach the ticket queue. Everything else is silently discarded and logged.

**Why this priority**: Without this layer, the AI replies to newsletters and outreach, damaging sender reputation and creating operational chaos for every connected brand. This is the core safety guarantee of the product.

**Independent Test**: Connect a real Gmail inbox, send 10 emails of mixed type (5 genuine support, 5 marketing/outreach), wait one poll cycle; verify exactly 5 tickets created and 5 rows in the guardian log with `decision=blocked`.

**Acceptance Scenarios**:

1. **Given** an email from `hello@klaviyo-mail.io` with subject "Your Weekly Newsletter", **When** it arrives in the connected inbox, **Then** no ticket is created, a guardian log row is written with `classification=newsletter`, and the email_poller moves to the next message.
2. **Given** an email from `customer@gmail.com` with subject "My order #1234 hasn't arrived", **When** it arrives, **Then** a ticket is created with `classification=customer_support` and `confidence >= threshold`.
3. **Given** an email that passes Layers 1–3 but whose body contains only a generic sales pitch (no order/product context), **When** the AI classifier runs, **Then** it is classified as `outreach` and no ticket is created.
4. **Given** an email classified as `customer_support` but with `confidence < threshold`, **When** the confidence gate runs, **Then** the email is quarantined (not blocked, not auto-replied), and an operator sees it in the Quarantine Queue.

---

### User Story 2 — Support-Only Mode Is the Production Default (Priority: P1)

A brand admin logs into the dashboard. Support-Only Mode is on by default. In this mode the system never auto-replies unless the email is classified as `customer_support` with confidence above the configured threshold. The admin can turn the mode off for testing but the default ensures safe production behavior.

**Why this priority**: Every new brand that connects Gmail inherits safe defaults. No configuration is required to prevent the AI from spamming newsletters.

**Independent Test**: Create a new brand with no custom settings; confirm `support_only_mode=true` and `confidence_threshold=0.75` are the defaults returned by the settings API; send a promotional email and confirm it is blocked without any settings changes.

**Acceptance Scenarios**:

1. **Given** a newly created brand with no custom settings, **When** `GET /api/v1/settings/email-filter` is called, **Then** `support_only_mode=true` and `confidence_threshold=0.75` are in the response.
2. **Given** support-only mode is enabled, **When** an email classified as `promotion` with confidence `0.9` arrives, **Then** no ticket is created and no AI reply is sent.
3. **Given** support-only mode is disabled, **When** an email classified as `promotion` arrives, **Then** layers 1–3 still run; only the AI classification gate is bypassed.

---

### User Story 3 — Quarantine Queue Lets Operators Review Low-Confidence Emails (Priority: P2)

An email arrives that looks like a real customer inquiry but uses unusual phrasing. The AI classifier returns `customer_support` with confidence `0.60`, below the `0.75` default threshold. Instead of blocking it outright or auto-replying, the system quarantines it. An operator can open the Quarantine Queue, read the email, and decide to promote it to a ticket or discard it.

**Why this priority**: Avoids both false negatives (blocking a real customer) and false positives (auto-replying to spam). Operators stay in control of edge cases.

**Independent Test**: Set `confidence_threshold=0.9` via settings; send an email the AI scores at `0.75`; verify no ticket is created, one quarantine record exists, and the operator dashboard shows the email in Quarantine Queue with the reason "low_confidence".

**Acceptance Scenarios**:

1. **Given** an email with `confidence=0.65` and threshold `0.75`, **When** the guardian processes it, **Then** a quarantine record is created with `reason=low_confidence` and no ticket or AI reply is generated.
2. **Given** an operator opens the Quarantine Queue, **When** they click "Promote to Ticket", **Then** a support ticket is created from the quarantined email.
3. **Given** an operator opens the Quarantine Queue, **When** they click "Discard", **Then** the quarantine record is marked dismissed and the email is not processed further.
4. **Given** a quarantined email, **When** 7 days pass without operator action, **Then** it is auto-expired and not processed.

---

### User Story 4 — Brand Owners Configure Guardian Rules Per-Tenant (Priority: P2)

A brand admin navigates to Settings → Email Filters. They see new controls: a confidence threshold input (0–1), a support-only mode toggle, and an auto-reply enable/disable toggle. They can adjust these without touching any other brand's configuration. All changes take effect on the next poll cycle.

**Why this priority**: Different brands have different risk tolerances. A high-volume brand may want `confidence_threshold=0.85`; a low-volume brand may want `0.60` to avoid missing any real customers.

**Independent Test**: Update `confidence_threshold=0.9` via PATCH; resend the same email that previously passed; confirm it is now quarantined instead of creating a ticket.

**Acceptance Scenarios**:

1. **Given** a brand admin submits `PATCH /api/v1/settings/email-filter` with `{"confidence_threshold": 0.9, "support_only_mode": true}`, **When** the next email poll runs, **Then** the new threshold is applied.
2. **Given** brand A sets `confidence_threshold=0.60` and brand B sets `confidence_threshold=0.90`, **When** the same email arrives in each inbox, **Then** it may be quarantined for brand B but proceed for brand A — with zero cross-tenant leakage.
3. **Given** `auto_reply_enabled=false`, **When** any email arrives (even genuine support), **Then** a ticket is created but no AI reply is sent.

---

### User Story 5 — Guardian Analytics Visible in Dashboard (Priority: P3)

An operator opens the dashboard. The "Filtered Emails" widget shows a breakdown across all five layers, including quarantine counts and AI classification results. They can see at a glance how many emails the AI classifier caught that passed layers 1–3, and how many are sitting in the quarantine queue awaiting review.

**Why this priority**: Operators need observability to trust the system. Without visibility, they cannot tell if the guardian is misconfigured.

**Independent Test**: After processing 20 emails of mixed types, open the dashboard; verify the widget shows correct counts for `blocked`, `allowed`, `quarantined`, and `prevented_loops` that match the audit log table.

**Acceptance Scenarios**:

1. **Given** the dashboard is open, **When** the "Filtered Emails" widget loads, **Then** it shows `total_blocked`, `total_allowed`, `quarantined`, `prevented_loops`, and `by_reason` breakdown including `ai_classification` and `low_confidence` reasons.
2. **Given** 3 emails are in the quarantine queue, **When** an operator views the widget, **Then** the quarantine count is `3` and a "Review Quarantine" link is visible.

---

### Edge Cases

- What happens when the AI classifier is unavailable or times out? System fails open: email treated as `classification=unknown`; when `support_only_mode=true` it is quarantined, not blocked, so a real customer is never silently dropped.
- What happens when `confidence_threshold=0.0`? The confidence gate is effectively disabled — all emails classified as `customer_support` proceed regardless of score.
- What happens when `support_only_mode=false` and classifier returns `promotion`? Layers 1–3 still apply; the AI classification gate result is recorded but does not block the email.
- What happens when an email has no body text for the classifier? Classified as `unknown`; quarantined when `support_only_mode=true`.
- What happens when a whitelisted sender's email gets a low confidence score? Whitelist bypass still occurs for layers 2–4 reputation checks, but the confidence gate still applies — a low-confidence result quarantines the email.
- What happens if the quarantine table grows unbounded? Records older than 7 days are auto-expired by a scheduled cleanup; expired records are not promoted or discarded, just marked `status=expired`.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST classify every inbound email into one of: `customer_support`, `promotion`, `newsletter`, `outreach`, `spam`, `automation`, `unknown` before any ticket is created.
- **FR-002**: System MUST assign a numeric confidence score (0.0–1.0) to each classification result.
- **FR-003**: System MUST block (silently discard) emails classified as non-`customer_support` when `support_only_mode=true`.
- **FR-004**: System MUST quarantine emails classified as `customer_support` with `confidence < confidence_threshold` rather than creating a ticket or sending an AI reply.
- **FR-005**: System MUST default to `support_only_mode=true`, `confidence_threshold=0.75`, and `auto_reply_enabled=true` for all brands with no custom settings.
- **FR-006**: System MUST expose a Quarantine Queue where operators can promote quarantined emails to tickets or discard them.
- **FR-007**: System MUST log every classification result (email, classification, confidence, decision, triggering layer/reason) to an audit table.
- **FR-008**: System MUST apply per-tenant settings exclusively — no brand's configuration can affect another brand's email processing.
- **FR-009**: System MUST continue processing emails when the AI classifier is unavailable; affected emails are quarantined when `support_only_mode=true`, not silently dropped.
- **FR-010**: System MUST expose `confidence_threshold`, `support_only_mode`, and `auto_reply_enabled` controls via the Settings API and Settings UI.
- **FR-011**: System MUST auto-expire quarantined emails that have not been actioned after 7 days (mark `status=expired`).
- **FR-012**: Dashboard widget MUST include `quarantined` count, `ai_classification` reason, and `low_confidence` reason in addition to existing filter reasons.
- **FR-013**: Operators MUST see the classification reason (layer, rule, confidence) for every filtered or quarantined email.

### Key Entities

- **GuardianDecision**: Result of the full 5-layer evaluation for one email. Attributes: brand_id, sender_email, thread_id, classification (enum), confidence (float), decision (allowed/blocked/quarantined), reason, layer_triggered, created_at.
- **QuarantineRecord**: An email held for human review. Attributes: brand_id, sender_email, subject, body_preview (first 500 chars), classification, confidence, status (pending/promoted/discarded/expired), actioned_by, actioned_at, expires_at (created_at + 7 days), created_at.
- **GuardianSettings** (extends existing per-brand system_settings): New fields — support_only_mode (bool, default true), confidence_threshold (float 0–1, default 0.75), auto_reply_enabled (bool, default true).

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Zero promotional or newsletter emails create support tickets when `support_only_mode=true`, measured over a 7-day production run.
- **SC-002**: Genuine customer support emails (refund requests, order issues, shipping questions) reach the ticket queue within 90 seconds of arriving in the connected inbox.
- **SC-003**: No AI reply is sent to any email classified as non-`customer_support` when `support_only_mode=true`.
- **SC-004**: Operators can review and action all quarantined emails within 2 clicks from the dashboard.
- **SC-005**: Changes to `confidence_threshold` or `support_only_mode` take effect within one poll cycle (60 seconds).
- **SC-006**: Every evaluated email has a corresponding guardian audit log entry — 0% unlogged evaluations.
- **SC-007**: False negative rate (genuine support emails quarantined or blocked) is below 5% in steady-state operation.

---

## Assumptions

- The AI classifier uses the Mistral API already in the stack — no new LLM provider or package is added.
- A single lightweight classifier call per email (2–5 seconds) is acceptable; the 60-second poll cycle provides sufficient headroom.
- Quarantine UI is a new page or section within the existing ai-ops-console admin app, not a separate product.
- This feature wraps (does not replace) the existing `email_filter_service.py` — layers 1–3 and loop detection remain intact.
- Per the feature 005 constitution decision: whitelisted domains bypass layers 2–4 reputation checks, but the AI classification gate (layer 4) still runs on the email body; a low-confidence result may still quarantine the email.
- `auto_reply_enabled=false` suppresses AI-generated email replies but still creates tickets, enabling human agents to respond manually.
