# tResolv — Phase 4: Cancellation Autopilot Execution

**Type:** Product implementation (automatic execution, gated by explicit merchant activation). Cancellation only — refunds/exchanges/address changes/customer profile changes remain human-approved.
**Follow-up to:** `specs/007-autopilot-automation/readiness-and-category-controls.md`

---

## What changed

1. `backend/migrations/047_cancellation_autopilot.sql` — adds `brands.cancellation_autopilot_enabled BOOLEAN DEFAULT false`. Created only, **not applied**.
2. `backend/src/api/routes/v2_brands.py` — `_compute_cancellation_readiness()` helper (extracted, reused by both `/analytics` and the new endpoints); `POST /{brand_id}/automation/cancellation/enable`; `POST /{brand_id}/automation/cancellation/disable`.
3. `backend/src/services/return_actions_integration.py` — `_maybe_autopilot_cancel()`, called from the single existing "CANCEL QUEUED" branch.
4. `dashboard/src/pages/Automation.jsx` (+ `services.js`, `useApi.js`) — real OFF/Not ready/Ready for review/ON states, activation confirmation dialog, ON-state stats card, disable button.
5. `backend/tests/test_cancellation_autopilot.py` — 19 new tests.

---

## Cancellation Autopilot flow

**Unchanged (Autopilot not enabled):** customer requests cancellation → Luna verifies order live against Shopify → policy check → action staged (`pending`) → human approves → Shopify cancellation. Byte-for-byte identical to before this task (`test_copilot_behavior_unchanged_without_autopilot_column`).

**New (brand has explicitly enabled Autopilot):** the same request reaches the exact same "CANCEL QUEUED" branch — the only place in `return_actions_integration.py` where, by construction, every hard eligibility rule below has already passed. From there:

1. The action is staged exactly as before (`actions_service.create_action`, `status="pending"`) — the audit record exists regardless of what happens next.
2. `_maybe_autopilot_cancel()` re-reads `brands.cancellation_autopilot_enabled` fresh from the database (not from anything cached or passed in).
3. If enabled, it calls `actions_service.approve_action(tenant_id, action_id, approved_by="autopilot", idempotency_key=f"autopilot-{action_id}")` — the **same function** a human's Approve click calls. No second execution path exists.
4. On Shopify's real confirmed success, the action transitions to `executed` (identical to human approval) and the customer is told, only now: *"Done! Your order #1013 has been cancelled successfully."*
5. On any failure (Shopify rejection, order changed mid-flight, etc.), `approve_action`'s existing failure handling marks the action `failed` with the real error preserved internally, and the customer is told: *"I couldn't complete the cancellation automatically, so I've sent this to our team for review."* — no success claim, no fabricated response-time promise.

If the order is fulfilled, or the store has a free-text cancellation policy requiring human judgment, execution never reaches `_maybe_autopilot_cancel()` at all — those branches (unchanged) stage for manual review exactly as they did before Autopilot existed.

---

## Safety gates

| Rule (task §3) | Enforcement |
|---|---|
| Correct tenant/store | `tenant_id`/`brand_id` threaded through unchanged from the authenticated ticket/chat session; `approve_action` re-validates tenant ownership of the action row. |
| Valid Shopify connection | `approve_action` calls `shopify_service.get_client_for_tenant(tenant_id)`, which fails closed (`ShopifyError`) if not connected. |
| Order exists / belongs to store / still eligible | `check_return_eligibility()` (unchanged, existing) — the CANCEL QUEUED branch is only reached when this already returned live-verified data. |
| Not fulfilled/shipped | `is_unfulfilled` check (unchanged, existing) — structurally excludes fulfilled orders from this branch entirely. |
| Store cancellation policy permits it | `get_custom_policy_text()` (unchanged, existing) — non-empty policy text routes to manual review *before* the autopilot hook, even with Autopilot enabled. |
| Shopify state freshly verified | `check_return_eligibility()`'s live Shopify fetch, plus `approve_action`'s own re-fetch before mutating. |
| Not already executed/rejected/being processed | `approve_action`'s atomic `status=eq.pending` conditional-update claim (unchanged, existing) — the same protection against double-execution every action in this system already has. |
| Autopilot enabled | New: `_maybe_autopilot_cancel()` reads `brands.cancellation_autopilot_enabled` fresh on every request. |

**Model-independent authorization:** Luna's role is limited to identifying customer intent and supplying `order_id`/`email`; every check above runs in Python before any Shopify mutation. `_maybe_autopilot_cancel()` has no input from the LLM's own output — it only receives the already-staged action from deterministic backend logic.

**Kill switch:** disabling sets `cancellation_autopilot_enabled=false`; the very next request reads it fresh and falls through to normal staging (`test_turning_off_prevents_next_automatic_execution`). An action already claimed `approved` by a concurrent in-flight request is untouched — `approve_action`'s atomic claim and Shopify call proceed to completion regardless of a setting flip, exactly as they do for a human-approved action today.

---

## Files changed

- `backend/migrations/047_cancellation_autopilot.sql` (new, not applied)
- `backend/src/api/routes/v2_brands.py`
- `backend/src/services/return_actions_integration.py`
- `backend/tests/test_cancellation_autopilot.py` (new)
- `dashboard/src/api/services.js`
- `dashboard/src/hooks/useApi.js`
- `dashboard/src/pages/Automation.jsx`

---

## Tests

19 new tests in `test_cancellation_autopilot.py`, covering all 18 named scenarios:

- Enable: blocked when readiness insufficient / succeeds when sufficient / requires authentication / blocked for wrong tenant / requires a connected Shopify store / blocked when not entitled.
- Disable: always allowed regardless of readiness; blocked for wrong tenant.
- Execution: disabled Autopilot never auto-executes; enabled Autopilot executes an eligible cancellation exactly once (asserts the real `approved_by`/`idempotency_key` passed to `approve_action`); a duplicate request short-circuits before Autopilot is ever reached; a fulfilled order escalates without an autopilot attempt; a Shopify failure during an attempt escalates without ever claiming success and without a fabricated response-time promise; a custom store policy escalates even with Autopilot enabled; Copilot behavior is unchanged (byte-for-byte, including with the flag column entirely absent — the pre-migration state); refund and exchange handling never reference the autopilot hook (one structural, one behavioral test); turning Autopilot off prevents the next automatic execution; the autopilot call passes through the request's own real `tenant_id`.

All Shopify interaction is mocked via `actions_service.approve_action`/`create_action`; no real Shopify calls occur in tests.

Targeted re-run of pre-existing suites most likely to interact with these changes — `test_customer_voice_analytics.py` (readiness), `test_action_lifecycle_safety.py` (action state machine), `test_no_fabricated_response_time_promises.py` (wording lint) — all still pass unmodified.

## Full backend suite

**800 passed, 0 failed** (781 baseline + 19 new).

## Frontend build

`npx vite build` — succeeds, no errors.

---

## Migration / deployment requirements

- `047_cancellation_autopilot.sql` must be applied (e.g. `supabase db push`) before any merchant can actually enable Cancellation Autopilot. Until then, `cancellation_autopilot_enabled` reads as absent/falsy everywhere (`supabase_select`'s plain `SELECT *` behavior), so the feature is fully off — safe to deploy the code ahead of the migration.
- `046_action_edit_tracking.sql` (from the prior task) remains unapplied — unrelated to this feature, not touched.

## Anything still blocked

- Nothing for Cancellation Autopilot itself — end-to-end code is complete and tested; only the migration application is a manual deployment step (intentionally not automated, per STOP CONDITIONS).
- Refund/Exchange Autopilot remain explicitly out of scope, unbuilt, and still show "Coming soon" — no fake functionality was added for either.
