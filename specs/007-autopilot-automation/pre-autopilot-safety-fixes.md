# tResolv — Pre-Autopilot Safety Fixes

**Type:** Security fix (Task 1) + verification research (Tasks 2-4). No Autopilot code, no new OAuth scopes shipped, no production migrations.
**Follow-up to:** `specs/007-autopilot-automation/research.md`

---

## Task 1 — Secured `/api/brand-actions/*`

### Confirmed: the finding was real, and worse than scoped

Tracing `/api/brand-actions/approve/{id}` found that **every route in `backend/src/api/routes/brand_actions.py`** — not just `/approve` — had zero authentication (`Depends(...)`) despite being registered live at `/api/brand-actions/*` in `backend/main.py:313-315`. That includes:

- `POST /approve/{id}` and `POST /reject/{id}` — execute/reject a real Shopify refund/cancel/address-change
- `POST /manual` — creates a fabricated pending action for **any** `brand_id`
- `GET /pending`, `GET /stats`, `GET /by-brand/{brand_id}`, `GET /{action_id}` — leak customer PII (email, name, order id, AI reasoning) across every tenant when no `brand_id` filter is given
- `DELETE /{action_id}` — soft-deletes (rejects) any brand's action
- `GET /logs/{action_id}` — leaks the action's audit trail

Confirmed no other route already provided a secure implementation of *this table's* flow — `v2_actions.py` secures the parallel `actions` table, not `brand_actions`. Confirmed via `grep -rn "brand-actions\|brand_actions" dashboard/src/` that the dashboard frontend **never calls this router at all** — so securing it carries zero risk of breaking an existing UI flow.

### Fix

1. **`backend/src/api/routes/brand_actions.py`** — every route now requires `Depends(require_agent_or_admin)` (the same dependency `v2_actions.py` already uses) plus an explicit `context.brand_ids` ownership check before touching any brand-scoped data, matching the pattern already established and tested in `test_actions_brand_isolation.py`. `/stats` and `/pending` (which previously defaulted to *all brands on the platform* when no `brand_id` was given) now default to only the caller's own `context.brand_ids`.

2. **`backend/src/services/multi_brand_actions.py`** — `approve_action()` and `reject_action()` had no atomic claim at all (a plain check-then-act: read status, then later `supabase_update` unconditionally). Fixed by adding the same `status=eq.pending` conditional-UPDATE claim pattern already used in `actions_service.py`/`v2_actions.py`, so two concurrent approve calls (double-click, retry) can no longer both execute a real Shopify mutation against the same action. Also fixed a related bug this surfaced: several early-return failure paths inside `approve_action()` (brand not found, no order id, no address, unknown action type) previously left the row silently stuck at whatever status with no way to retry — now every exit path lands on a terminal `failed` status via a small `_fail()` helper.

### Tests

New file `backend/tests/test_brand_actions_security.py`, 5 tests, all passing:
- `test_unauthenticated_approval_is_rejected` — no auth header → 401, no DB/Shopify call
- `test_authenticated_wrong_tenant_approval_is_blocked` / `..._reject_is_also_blocked` — a real authenticated admin of a *different* brand → 403, no DB/Shopify call
- `test_valid_merchant_approval_passes_the_ownership_gate` — legitimate same-brand approval is not blocked by the ownership check
- `test_duplicate_approval_only_executes_once` — service-level test with a stateful fake DB proving the atomic claim: two back-to-back `approve_action()` calls result in exactly one Shopify `cancel_order` call, the second call gets `"Action already executed"`

Also re-ran the existing `test_actions_brand_isolation.py` (the equivalent fix already shipped for `v2_actions.py`) plus the related action-lifecycle/hardening suites — all still passing, confirming this fix didn't regress the parallel, already-secured `actions` table's behavior.

---

## Task 2 — Shopify OAuth scope audit

**Correction to the prior research's file citation:** the scope string is built in `backend/src/services/shopify_oauth.py:94`, not `shopify_auth.py` (that file is only the OAuth *callback*/token-exchange handler). Confirmed: `scopes = os.getenv("SHOPIFY_SCOPES", "read_products,write_products")`, used by exactly one caller — `GET /{brand_id}/shopify/oauth/start` (`v2_brands.py:535`) — which both the onboarding wizard and Settings "Connect"/"Reconnect" buttons hit. No brand-specific override, no hardcoded broader string anywhere, no `.env.example` for the backend. A second scope list (`shopify_scope_service.py`'s `REQUIRED_SCOPES`) exists but is a decoy for this purpose — it only feeds the post-connect health-check display, never the authorize URL, and it's itself missing every write scope.

**Confirmed missing, with Shopify's own scope model:**

| Missing scope | Needed for |
|---|---|
| `write_orders` | Every order mutation: refund, cancel, address update, reopen — **and** every read these call internally (`get_order`, order counts, WISMO fulfillment/tracking lookups), since none of that is covered by `read_products,write_products` either |
| `write_draft_orders` | The exchange flow (`create_exchange_draft_order`'s `draft_orders.json` calls) — **a separate scope from `write_orders`**, easy to miss |

Per Shopify's access-scope model, requesting `write_orders` implicitly grants `read_orders` (same for `write_draft_orders`/`read_draft_orders`) — the fix is two scope strings, not four. Inventory and fulfillment data are read as fields embedded in the `products.json`/`orders.json` payloads already being fetched, not via separate endpoints, so no additional `read_inventory`/`read_fulfillments` scope is needed for what's actually implemented. **Also found:** `write_products` (currently granted) is dead weight — no code anywhere writes to `products.json`.

**Not shipped, per the task's explicit instruction not to blindly add scopes.** If the team decides to add `write_orders,write_draft_orders`:
- **Not silent for existing merchants.** Shopify access tokens are scope-locked at the time of consent; widening the requested scope only affects the *next* authorize round-trip. Every already-connected brand keeps its narrower token until that merchant clicks "Reconnect" and re-approves.
- **The reconnect mechanism already exists and needs no new code** — `GET /{brand_id}/shopify/oauth/start` rebuilds the authorize URL from the live `SHOPIFY_SCOPES` value on every call, and Settings' existing "Reconnect" button already drives it.
- **Two real gaps to close alongside the scope change**, or already-connected brands will see order actions silently fail with no in-product explanation: (1) `shopify_scope_service.py`'s `REQUIRED_SCOPES` dict (which drives the Settings "missing scopes" banner) doesn't include any write scope, so it would keep reporting a brand as "healthy" even without `write_orders`; (2) `ShopifyClient._handle_response()` has no scope-aware 403 handling on the action-execution path — the import flow already has this (`_is_scope_error()` in `shopify_import_service.py`), but refund/cancel/address-change/exchange do not, so a scope-denied call falls through to a generic error with no "please reconnect" signal surfaced to the merchant.

---

## Task 3 — Product recommendation verification

`pytest tests/test_product_recommendations.py tests/test_product_discovery.py -v` → **33 passed, 0 failed.**

Traced the five example queries against the live routing code (not re-derived from the prior audit):

| Query | Tool fires? | What actually happens |
|---|---|---|
| "Do you have anything similar?" | No | Recommendation keyword matches, but no anchor product extractable from the phrasing → falls through to a fully ungrounded LLM reply, no guard intervention |
| "Show me something like this" | No | Anchor pattern matches "something like this" but the candidate is the pronoun "this," explicitly filtered out → same ungrounded fallthrough |
| "Do you have this in another color?" | **Yes — but the wrong tool.** *(correction to the prior audit)* | Doesn't match the recommendation keyword set; matches the plain inventory-lookup gate instead, which greedily captures "this in another color" as if it were a literal product name → an honest "couldn't find that" result, but via a different mechanism than "no tool fires" |
| "What about a smaller size?" | No | Matches none of the product-tool keyword sets; only the separate sizing-chart engine fires, which needs height/weight to be useful and does no Shopify product/variant lookup |
| "What would you recommend with this?" | No | Classified as "similar" intent (not "complementary" — the complementary keyword list needs phrases like "goes well with"), then the pronoun anchor is filtered the same as above |

**Confirmed real gaps (no new ones invented, one prior framing corrected):**
1. **Pronoun resolution is the sharpest gap.** Not just "no clarification happens" — the one safety net that exists for this feature (`_enforce_no_ungrounded_recommendation`) structurally cannot fire when no tool was called at all, so these fall all the way through to an unguarded, ungrounded LLM reply.
2. **Variant/color queries** are misrouted through the inventory-lookup gate with a garbage extracted argument, rather than simply "unrouted" as previously described — same practical outcome, different mechanism, matters for whoever fixes it.
3. **No cross-turn category narrowing** (already documented in the code's own docstring) — same root cause as #1.

Tenant/store scoping and live-data freshness were independently re-verified (not just re-asserted): every recommendation/discovery/inventory call requires per-brand credentials with no fallback to a shared default, a real behavioral test (`test_recommendations_use_the_specific_brands_credentials_only`) proves two brands' calls never cross-contaminate, and the 30-second order cache elsewhere in the codebase does not apply to product data — every product fetch is live.

---

## Task 4 — Shopify order/customer mutation capability matrix (re-verified)

Exhaustive grep of every Shopify write call in the backend (10 total, including a duplicate/legacy client in `brand_manager.py`) confirms: **zero code anywhere writes to a `customers.json`/customer-profile object.** `update_shipping_address` exclusively targets `PUT orders/{id}.json` with a `shipping_address` body key — never `customer.default_address`. The "order shipping address is a snapshot, distinct from the customer's default address" claim holds both in this codebase (nothing conflates them) and independently in Shopify's own data model (the two are separate objects regardless of what any client does).

| Capability | Shopify operation | Scope | Implemented? | Safe to automate? | Human approval? |
|---|---|---|---|---|---|
| Shipping address change (order) | `PUT orders/{id}.json` | `write_orders` | **Yes** — `shopify_service.py:732-819` | Conditionally, pre-fulfillment only | Yes (staged action) |
| Customer name change | `PUT customers/{id}.json` | `write_customers` (separate grant) | **No** | Conditionally — affects every past/future order for that customer, not just the one in the ticket | Would need it, unbuilt |
| Customer email change | `PUT customers/{id}.json` | `write_customers` | **No** | More cautiously than name — email is also the storefront login | Would need stronger identity verification than typical, unbuilt |
| Order contact info override (order-level email/phone, distinct from customer.email) | `PUT orders/{id}.json` | `write_orders` — **same scope as address change, not `write_customers`** | **No** | Yes — the correctly-scoped tool for "wrong email/phone on this order" requests, which today have no automation path short of a full customer-profile edit | Yes, if built |
| Order note | `PUT orders/{id}.json` | `write_orders` | **No** | Yes — internal/merchant-visible only, no customer-facing effect | No, low-risk audit-trail use |
| Order tags | `PUT orders/{id}.json` | `write_orders` | **No** | Conditionally — Shopify's `tags` field is a full overwrite, not additive; needs a get-then-merge, or it will silently delete a merchant's existing tags | No for an additive tag, but the merge logic needs review first |
| Cancel reason | `POST orders/{id}/cancel.json` | `write_orders` | **Yes, already shipped** — `shopify_service.py:670-730` | n/a, not a gap | Yes (staged action) |

**Two things this verification pass corrected or found beyond the prior research:**

1. **Good news the prior research didn't know:** the pre-fulfillment guard for address changes already exists in code (`shopify_service.py:752-756`, raises before ever calling Shopify if `fulfillment_status == "fulfilled"`), duplicated in the legacy `brand_manager.py` client, and the LLM prompt layer is separately told not to *offer* the action once an order is fulfilled. The prior research's "must be a branch in the flow" language implied this needed building — it's already built and is more conservative than Shopify's own API behavior (Shopify would otherwise silently accept and store the update with zero effect on an already-created shipping label).

2. **New finding, worth a ticket regardless of Autopilot:** `process_refund()`'s `restock: bool = False` parameter is dead — the refund payload never includes `refund_line_items`, so items are never actually marked for restock no matter what's passed. (`cancel_order()`'s equivalent `restock` param, by contrast, is correctly wired through.) Not fixed here per the task's "verify, don't implement" scope — flagged for the team to either fix or remove the misleading parameter.

---

## What blocks the next Autopilot phase

Restating and updating `research.md`'s Phase 0 prerequisites in light of this pass:

- [x] `/api/brand-actions/*` unauthenticated route — **closed this task.**
- [ ] Shopify OAuth scope (`write_orders`, `write_draft_orders` missing) — **confirmed real, not yet shipped.** Before shipping: also widen `REQUIRED_SCOPES` (or the health-check logic doesn't detect the gap) and add scope-aware 403 handling to the action-execution path (or existing merchants get silent failures after the scope widens but before they reconnect).
- [ ] The readiness-computation denominator bug (`status='failed'` excluded from the approval-rate calculation) — untouched this task, still open from `research.md`.
- [ ] Edit-tracking (`was_edited`) columns — untouched this task, still open from `research.md`.
- [ ] Product recommendation conversational gaps (pronoun resolution, variant/color routing) — confirmed real this task; doesn't block Autopilot for Shopify-write categories, but would undercut an "Autopilot for product questions" promise specifically.
