# tResolv — Autopilot Readiness + Category Controls

**Type:** Product implementation (readiness UI, category controls, customer messaging). No Autopilot execution, no OAuth scope changes, no production migration applied in this task.
**Follow-up to:** `specs/007-autopilot-automation/pre-autopilot-safety-fixes-implementation.md`

---

## Prerequisite status (Phase 0)

- **`write_draft_orders`**: Not enabled. Verified two ways — the corrected app scope list from the prior task, and (independently) the live `shopify_granted_scopes` actually granted to the one connected brand in production (queried directly): `write_draft_orders` is absent from both. `write_draft_orders` requires manual Shopify Partner Dashboard configuration — no code or MCP tool available in this session can enable it. Once enabled, the existing merchant would need to explicitly Reconnect Shopify → OAuth → approve new permissions; no new Admin API key, no manual token paste, no silent invalidation.
- **`046_action_edit_tracking.sql`**: Confirmed **not applied** — queried `information_schema.columns` directly against the production database; neither `was_edited` nor `approved_extracted_data` exist on `actions`. Reported as a deployment prerequisite, not applied (per explicit instruction not to apply automatically).

---

## Phase 1 — Autopilot Readiness (backend)

Extended `GET /{brand_id}/analytics` (`v2_brands.py`) minimally rather than building a parallel analytics system — reuses the exact `cancel_executed`/`cancel_rejected`/`cancel_failed` counts already computed for `autopilot_readiness` (fixed in the prior task to never drop failures from the denominator). Added a new `category_readiness` field:

```
category_readiness.cancellation = {
  category, mode: "copilot",
  total_requests, successful, escalated, failed_executions,
  approval_rate, status, min_sample
}
```

- `escalated` = human rejections (normal, expected judgment calls — not itself a red flag).
- `failed_executions` = genuine Shopify execution failures (a human approved, but the Shopify call itself failed) — this is what distinguishes "almost there" from "ready for review," not rejections.
- `status` — three values, using only real data and the existing `_AUTOPILOT_MIN_SAMPLE` threshold (no new invented number):
  - `not_ready`: sample below the existing minimum.
  - `almost_there`: sample sufficient, but at least one real execution failure in the history.
  - `ready_for_review`: sample sufficient, zero execution failures.

Only `cancellation` is computed (per the task's "start with cancellation, don't over-engineer" instruction) — `_READINESS_CATEGORIES` documents the mapping for `refund`/`exchange` as a one-line extension point when those categories are ready to be wired, without building unused framework now.

`autopilot_readiness` (the existing field, read by `CustomerVoice.jsx`) is untouched for backward compatibility — `category_readiness` is additive, always present once a brand has any cancellation history (even below the sample floor, since the "why isn't this ready" UX needs the real numbers regardless).

---

## Phase 2–5 — Dashboard (frontend)

New page `dashboard/src/pages/Automation.jsx`, added to the sidebar (`Automation`, placed next to Customer Voice) and routed at `/automation` — following the existing page/route/nav pattern exactly, no new dashboard architecture.

- **Category controls** (Phase 2): Cancellation / Refunds / Exchanges rows, each labeled "Copilot" (the only mode that's ever actually functioned). Cancellation has a working "Review readiness" toggle; Refunds/Exchanges show a genuinely disabled "Coming soon" — no enabled-looking control that does nothing.
- **Readiness detail** (Phase 1 UI): real stat blocks (`total_requests`, `successful`, `escalated`, `approval_rate`) pulled directly from `category_readiness.cancellation`, a status badge (🟢/🟡/⚪) matching the backend's `status`, and "Current mode: Copilot."
- **"Why isn't Autopilot ready"** (Phase 4): plain-language explanation text keyed off `status`, no internal counts/IDs/policy dumps exposed beyond the same numbers already shown in the stat blocks.
- **Future activation control** (Phase 3): "Enable Cancellation Autopilot" button — `disabled` unless `status === 'ready_for_review'`, and even then explicitly labeled "Not available yet — Autopilot execution hasn't launched." No execution is wired to this control in this task; it cannot do anything regardless of its enabled/disabled state.
- **Value proposition** (Phase 5): "Train → Verify → Approve → Automate" copy, framed as what the merchant chooses to do, not as Luna self-training.

`CustomerVoice.jsx`'s existing small readiness teaser (which had a permanently-disabled "Review Autopilot" button going nowhere) now links to `/automation` instead of duplicating the fuller experience — avoids two different UI treatments of the same underlying data.

---

## Phase 6 — Customer-facing wording during pending approval

Audited every customer-facing message constructed while an action is pending human approval (`return_actions_integration.py`'s `Tell the customer: '...'` instructions, `customer_success_agent.py`'s fallback replies, `v2_chat_widget.py`'s human-takeover notice) for exactly the failure mode the task named: promising a specific response time the product doesn't guarantee.

**Found and fixed 11 instances** of fabricated/vague timing across cancellation, refund, exchange, address-change, reship/lost-package, and restore-order messaging, and two AI-fallback replies and the chat-widget human-takeover notice — "within 2 hours," "within 24 hours," "usually under 2 hours," "shortly," "soon" — none backed by any real SLA/business-hours configuration anywhere in this codebase (confirmed in the prior research pass). Replaced with truthful, non-time-promising wording following the task's own example ("...We'll follow up once it's reviewed."), preserving every real, non-time-related detail (what was sent, why, what happens next).

**Deliberately left alone**: refund-appears-in-3–5-business-days (a real bank/processor fact, not an internal team promise) and tracking-updates-within-24-hours (real carrier behavior) — these aren't promises about how fast tResolv's team acts, so rewriting them would just remove accurate information.

A regression test (`test_no_fabricated_response_time_promises.py`) scans the actual source for the banned patterns across all three files, so a reintroduced fabricated promise fails CI rather than needing to be caught by exercising every message-generation branch individually.

---

## Tests

18 tests in `test_customer_voice_analytics.py` (12 existing + 6 new for `category_readiness`): real outcomes count correctly, execution failures stay in the denominator and drive `almost_there`, insufficient sample blocks readiness regardless of failures present, human rejections alone don't block "ready," cancellation/refund actions never contaminate each other's category metrics, and a dedicated test proves the endpoint never calls `supabase_update`/`supabase_insert`/`ShopifyClient` (no automatic mutation origin from this task). Plus 1 new lint-style test locking in the Phase 6 wording fix. Tenant isolation (`test_analytics_404s_for_a_brand_owned_by_another_tenant`) and every existing action-approval/execution test (`test_action_lifecycle_safety.py`, `test_partial_refunds.py`, `test_actions_brand_isolation.py`, etc.) re-run unchanged and still pass — nothing about the approval state machine, idempotency, or Copilot's actual behavior was touched.

---

## Full backend suite

**781 passed, 0 failed.**

## Frontend build

`npx vite build` — succeeds, no errors.

---

## Anything still blocked

- `write_draft_orders`: Partner Dashboard configuration, outside this session's reach.
- `046_action_edit_tracking.sql`: created, verified not applied, not applied by this task.
- No Autopilot execution path exists or was added — every category still requires human approval, unconditionally, today.
