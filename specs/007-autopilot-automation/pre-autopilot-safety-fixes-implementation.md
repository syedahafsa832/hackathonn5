# tResolv — Pre-Autopilot Safety Fixes: Implementation

**Type:** Implementation of the fixes identified in `pre-autopilot-safety-fixes.md`. No Autopilot, no automatic financial actions, no production migration applied, no OAuth scope changed.
**Follow-up to:** `specs/007-autopilot-automation/pre-autopilot-safety-fixes.md` (investigation) and `specs/007-autopilot-automation/research.md` (original architecture plan).

---

## 1. What changed

| Area | File(s) | What |
|---|---|---|
| Shopify scope health check | `shopify_scope_service.py`, `v2_brands.py` | Added `write_orders` to `REQUIRED_SCOPES`; `/shopify/health` now distinguishes `healthy` / `needs_permission_update` / `check_unavailable` / `connection_unavailable` / `not_connected` instead of collapsing every failure mode into a possibly-misleading "needs_permission_update" |
| Scope-aware 403 handling | `shopify_service.py`, `actions_service.py` | New `ShopifyErrorCode.MISSING_SCOPE`, a dedicated 403 branch in `_handle_response()` with a clean (non-raw) message; verified the existing atomic-claim/`_mark_failed` path already lands a 403 at a safe terminal `failed` state with no retry and no customer notification |
| Readiness denominator | `v2_brands.py` | `autopilot_readiness`'s `approval_rate` and sample-size gate now include `status='failed'` actions (previously silently excluded from both) |
| Action edit-tracking | `migrations/046_action_edit_tracking.sql` (unapplied), `actions_service.py` | `was_edited`/`approved_extracted_data` columns; best-effort, non-blocking write on the one existing edit surface (refund amount override) |
| Recommendation conversational gaps | `customer_success_agent.py` | New `_resolve_recent_product_anchor()` resolves pronoun/variant follow-ups against conversation history; extended `_enforce_no_ambiguous_product_claim` to force a clarifying question when nothing resolves |
| Refund restock parameter | `shopify_service.py` | Docstring-only: documented as non-functional (no behavior change) |

No changes to `shopify_oauth.py`, no new OAuth scopes requested, no production migration applied, no customer-profile mutation added, no Autopilot/automatic-approval logic added.

---

## 2. Shopify scopes added/verified

**None added.** Per the corrected authoritative Shopify app configuration provided for this task, `write_orders` is already granted at the app level — the prior research's "write_orders is missing" finding was wrong and has been retracted; the actual current code (`shopify_oauth.py:94`, unchanged) was never touched.

**Reconciliation performed:**
- Backend code requests `read_products,write_products` by default (env-overridable) via `get_authorize_url()` — unchanged, not verified against the live env var value (out of reach from this repo).
- `REQUIRED_SCOPES` (the *health-check's* required list, separate from what's requested at connect time) was missing `write_orders` entirely — **fixed**, since this is what the health page checks against a brand's live, actually-*granted* scopes (`shopify_granted_scopes`, fetched via `access_scopes.json`), independent of what the connect-time request string says.
- No code path anywhere was found that incorrectly assumes `write_orders` is missing (confirmed via `grep -rn "write_orders" backend/src/` — zero hits before this task's `REQUIRED_SCOPES` addition).
- **`write_draft_orders`**: traced `create_exchange_draft_order()` (`shopify_service.py`) — it calls `POST draft_orders.json`, `POST draft_orders/{id}/complete.json`, and `POST draft_orders/{id}/send_invoice.json`. Per Shopify's access-scope model, Draft Orders are a distinct resource from Orders, requiring `write_draft_orders` (which implies `read_draft_orders`) — **not** covered by `write_orders`. This scope is **not present** in the authoritative app scope list provided for this task. **Recommendation, not implemented:** the exchange flow's `write_draft_orders` requirement is genuinely real and still needs the app-level Shopify Partner Dashboard configuration updated (a business/platform-config action, not a code change — and per "Legacy install flow: OFF," the code's requested-scope string may not even be what governs actual grants; see `pre-autopilot-safety-fixes.md`'s Task 2 findings for the full mechanics). Not added here per the explicit instruction not to add scopes in this task.

**Scope usage audit (requested check):**

| Scope | Used by this codebase today? |
|---|---|
| `write_customers` | No — zero customer-profile writes anywhere (confirmed by exhaustive grep, see mutation-boundary section below) |
| `write_fulfillments` | No |
| `write_inventory` | No — the one `inventory_levels.json` call found is a GET (legacy/dev-fallback path only) |
| `write_inventory_shipments` / `write_inventory_shipments_received_items` / `write_inventory_transfers` | No |
| `write_order_edits` | No — line-item edits are not implemented |
| `write_products` | No — dead weight; nothing writes to `products.json` |
| `write_content` | No — the importer only reads pages/blogs/policies, never writes |
| `customer_write_orders` / `customer_read_orders` | No — these are Customer Account API scopes; this backend only uses the Admin API |
| `unauthenticated_read_content` | Not applicable to backend Admin API usage (Storefront-API-adjacent) |

None of these are removed in this task, per the explicit instruction — this is reported as an audit finding only. All are currently unnecessary for what's implemented; several (`write_order_edits`, `write_customers`) would become genuinely necessary if line-item edits or customer-profile changes are ever built.

**Current Shopify app scopes vs. backend-requested scopes vs. actually-required scopes** (final explicit table):

| Scope | In current app config | Requested by code default | Actually required by implemented code |
|---|---|---|---|
| `read_products` / `write_products` | Yes | Yes (default) | `read_products` yes; `write_products` no (unused) |
| `read_orders` / `write_orders` | Yes | Not in code default | Yes, both — every order mutation and the reads that gate them |
| `write_draft_orders` (+ implied `read_draft_orders`) | **No** | No | **Yes** — the exchange flow, confirmed by tracing `create_exchange_draft_order()` |
| `read_customers` / `write_customers` | Yes / Yes | No | No — unused |
| `read_fulfillments` / `write_fulfillments` | Yes / Yes | No | No — fulfillment data is read embedded in the order object under `read_orders`, no dedicated fulfillments endpoint is ever called |
| `read_content` / `write_content` | No (only `write_content` is in the provided list, not `read_content`) | Not by default | `read_content` yes (KB import); `write_content` no |
| `write_inventory` (+ shipments/transfers variants) | Yes | No | No |
| `write_order_edits` (+ `read_order_edits`) | Yes | No | No — not implemented |
| `customer_read_orders` / `customer_write_orders` | Yes | No | No — Customer Account API, unused |
| `unauthenticated_read_content` | Yes | No | Not applicable |

---

## 3. Existing-merchant reconnect behavior

No merchant connection was touched, invalidated, or silently changed. No new OAuth flow was built and no manual token-entry path was added — the existing `GET /{brand_id}/shopify/oauth/start` → Shopify consent screen → `POST /shopify/oauth/callback` flow (already present, already the only connection mechanism) is unchanged. Since no scope was actually added in this task, there is nothing for an existing merchant to reauthorize as a result of this work.

---

## 4. 403 handling

- `ShopifyClient._handle_response()` now classifies HTTP 403 as `ShopifyErrorCode.MISSING_SCOPE` with tResolv's own wording ("This Shopify connection is missing a permission this action needs. Reconnect Shopify to grant the required access.") instead of falling through to the generic `UNKNOWN_ERROR` branch, which previously echoed Shopify's raw error text.
- **No infinite retry**: verified `_request()`'s retry logic only retries `RATE_LIMITED`; a 403 raises immediately on the first attempt (regression test asserts the mocked HTTP call is made exactly once).
- **No false success, no partial completion**: `actions_service.approve_action()`'s existing `except ShopifyError` handler already routes any `ShopifyError` (including the new 403 case) to `_mark_failed()` — a genuine terminal `failed` status, never `executed`, never stuck at `approved`.
- **No approval bypass**: nothing changed about who can trigger an approval; the 403 only occurs *after* a human has already approved and Shopify rejects the resulting API call.
- **Customer never told anything false**: `_post_execution_notify()` (the only place a customer-facing confirmation email is ever sent) is only ever called on a genuine `execution_result.success` — verified this is never called on a `MISSING_SCOPE` failure. Today's architecture sends the customer nothing on any execution failure (not just this one) — silence, never a lie. Making the customer proactively aware of a stuck action would require a new notification decision (channel, timing, tone) that wasn't part of this task's scope; noted as a possible follow-up, not implemented here.
- **Merchant-facing reconnect path**: the dashboard's `Actions.jsx` already renders any `action.error_message` generically ("Action failed: {message}. Marked for manual review.") — since the new 403 message is itself the clean, reconnect-actionable text, this already surfaces correctly with zero dashboard changes needed.
- 5 new regression tests in `test_shopify_scope_error_handling.py` cover exactly these guarantees.

---

## 5. Readiness calculation fix

`v2_brands.py`'s `autopilot_readiness` (within `GET /{brand_id}/analytics`) now counts `status='failed'` cancel_order actions as a third bucket alongside `executed`/`rejected`: they count against `approval_rate` (a failure is a negative signal, exactly like a rejection) and toward the minimum-sample-size gate (a failure is still a real data point). Uses only the existing `actions.status` column — no new metric invented, no new data source. `eligible_cancellations`/`approval_rate` keep their existing meaning; a new `failed_executions` field is added purely for transparency (why the rate isn't higher than the executed count alone would suggest).

5 regression tests added to `test_customer_voice_analytics.py`, proving exactly the four behaviors requested: successes count correctly, failures count correctly, failures depress rather than inflate the rate (a 5-success/5-failure mix produces 50%, not 100%), and an insufficient sample (2 actions, one failed) still returns no readiness card.

---

## 6. Edit tracking

Migration `046_action_edit_tracking.sql` adds `actions.was_edited` (boolean, default false) and `actions.approved_extracted_data` (jsonb) — additive, non-destructive, `IF NOT EXISTS` throughout. **Not applied to production** — see §12.

Wired into `actions_service.approve_action()`'s refund branch (the only action type with any current edit surface — the human-entered `override_amount`): when the approver's amount differs from the AI-proposed `extracted_data.amount`, a best-effort, non-blocking `supabase_update` records `was_edited=true` and a snapshot of what was actually approved with. This write is deliberately **separate** from the atomic pending→approved claim and wrapped in its own try/except — since the migration isn't applied yet, writing these columns as part of the required claim update would break every refund approval in any environment where 046 hasn't landed; as a best-effort side write, it simply logs and continues instead. Once the migration is applied, this starts recording real data immediately with no further code change.

Cancel/address-change/reship/exchange/restore_order all still have zero edit surface in the approval UI — `was_edited` correctly defaults to `false` for these (accurate: they *can't* have been edited, not merely "unchecked").

9 regression tests across `test_partial_refunds.py`: edited amounts are recorded, unedited approvals are not, an override matching the original proposal isn't flagged as an edit, and — critically — a simulated "column doesn't exist yet" failure never breaks the approval itself.

---

## 7. Recommendation fixes

**A. Pronoun/context follow-ups** ("show me that one", "what about this one?", "do you have it in another size?"): `_resolve_recent_product_anchor()` scans backward through the conversation's own chat history (already passed into the agent as part of `query` by `v2_chat_widget.py`) for the most recently named real product, using the same extraction patterns already trusted for the current message, plus a declarative-sentence pattern for Luna's own prior affirmative replies ("The Winter Parka is in stock"). A resolved candidate is never trusted blindly — it still goes through the existing live Shopify title search before ever reaching a reply, so a wrong guess can only produce an honest "couldn't find that," never a fabricated result. When nothing resolves (no history, or history has no identifiable product), a deterministic clarifying question is forced through the existing `_enforce_no_ungrounded_recommendation`/`_enforce_no_ambiguous_product_claim` guards — never silence, never left to the model's discretion.

**B. Color/variant follow-ups** ("Do you have it in black?", "what about another color?", "same one in blue"): a new keyword gate (checked *before* the plain inventory-lookup gate) detects this class of phrasing and routes it through the same history-anchor resolution, then calls `get_inventory_status()` with the *resolved product name* — reusing that tool's already-correct, already-tested variant/option reporting — instead of the previous behavior, where the entire phrase ("this in another color") was passed to Shopify as if it were a literal product title. No availability, price, inventory, or variant data is ever fabricated — every answer still comes from the live tool call or the deterministic clarification.

13 regression tests added in `test_recommendation_context_resolution.py`, covering both cases directly (unit tests on the resolver) and end-to-end through the full agent (all 5 of the task's example queries, plus the "no resolvable context → clarify" cases for both paths). Re-ran the existing `test_product_recommendations.py`/`test_product_discovery.py`/`test_cross_ticket_memory_and_product_grounding.py` suites (64 tests) — all still pass, confirming no regression to already-correct behavior (in particular, `test_pronoun_only_anchor_does_not_trigger_a_search_for_the_literal_word_this` still passes: a pronoun-only message with *no* history still correctly avoids a literal-pronoun Shopify search).

---

## Shopify mutation boundary check (item 7 of the task brief)

Re-confirmed via exhaustive grep across `backend/src/` for any `customers.json`/`customerUpdate`-shaped write: **zero hits.** No customer-profile mutation exists or was added in this task. `update_shipping_address()` (the only address-change capability) still exclusively targets `PUT orders/{id}.json` with a `shipping_address` body key — never `customer.default_address` — and its pre-fulfillment guard (`if order.get("fulfillment_status") == "fulfilled": raise ShopifyError(...)`) was verified unchanged and still in place. No code in this task touches that guard or any customer-profile-adjacent path.

---

## 8. Files changed

```
backend/src/agent/customer_success_agent.py       (recommendation fixes)
backend/src/api/routes/v2_brands.py               (health check + readiness fix)
backend/src/services/actions_service.py           (edit tracking + 403 verification)
backend/src/services/shopify_scope_service.py     (REQUIRED_SCOPES + error classification)
backend/src/services/shopify_service.py           (403 branch + restock docstring)
backend/migrations/046_action_edit_tracking.sql   (new, unapplied)
backend/tests/test_customer_voice_analytics.py    (readiness regression tests)
backend/tests/test_partial_refunds.py             (edit-tracking regression tests)
backend/tests/test_shopify_scope_health.py        (updated + new health-check tests)
backend/tests/test_shopify_scope_error_handling.py (new — 403 handling)
backend/tests/test_recommendation_context_resolution.py (new — recommendation fixes)
```

No dashboard/frontend files were touched.

---

## 9. Tests

- Targeted approval/scope/readiness/recommendation test files: all pass (individually verified per task above).
- Full backend suite: see §10.
- No frontend build run — no frontend code changed (§11).

## 10. Full-suite result

**774 passed, 0 failed.**

## 11. Frontend build result

Not run — no frontend code was touched by this task.

## 12. Migration/deployment requirements

`backend/migrations/046_action_edit_tracking.sql` exists and is **not applied**. To apply when the team is ready: run the project's normal migration-apply step (e.g. `supabase db push`) against the target environment. No other migration, secret, auth architecture, or Supabase security policy was touched.

## 13. Anything still blocked / open

- `write_draft_orders` is genuinely required by the exchange flow and genuinely absent from the current app scope configuration — needs a Shopify Partner Dashboard change (not a code change) before exchanges can work end-to-end; flagged, not actioned, per this task's scope.
- Edit-tracking columns exist only in an unapplied migration — `was_edited`/`approved_extracted_data` will not actually persist anything until 046 is applied.
- Customer-facing notification on an execution failure (including a scope-related one) remains silent by design of the existing architecture — the task's "ensure the customer receives truthful wording" requirement is satisfied conservatively (never a lie, silence instead) rather than by building a new proactive notification path, which would need its own scoping.
- Everything else from `research.md`'s Phase 0 not covered by this task (consolidating the parallel `pending_actions`/`brand_actions` systems, generalizing readiness beyond `cancel_order`) remains open.
- No Autopilot, automatic-approval, or merchant-readiness-promotion logic was implemented, per the explicit instruction.
