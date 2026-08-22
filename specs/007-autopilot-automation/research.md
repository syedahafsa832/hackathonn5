# tResolv — Autopilot, Automation Modes & Shopify Order Actions

**Type:** Research + architecture planning (no code changes made)
**Status:** Draft for merchant/founder review
**Scope:** Category-level automation model, training→readiness→Autopilot journey, Shopify mutation capability audit, product recommendation audit, customer messaging, differentiation, phased rollout.

> Every factual claim about tResolv's current codebase below is cited with a `file:line` reference and was verified by direct code reading in this repository. Every claim about Wilmo is flagged with a confidence note — their site could not be fetched directly in this environment (network egress to wilmo.ai returned `403`/`EGRESS_BLOCKED`), so that section is built from web search results, not a direct page read. No numbers are invented anywhere in this document; where tResolv doesn't yet compute a number, that is stated explicitly rather than estimated.

---

## 1. What Wilmo's model actually does

*(Source: web search only — wilmo.ai was unreachable from this environment. Treat exact wording as near-verbatim but not a verified quote; treat the underlying claims as corroborated across multiple independent searches unless flagged otherwise.)*

**Three tiers, per ticket category — not Wilmo's original hypothesis of "Blocked" as the third name:**

| Their term | What it means |
|---|---|
| **Autopilot** | AI reads the ticket, takes the action in the merchant's systems, sends the reply, resolves it. No human involved. |
| **Copilot** | AI drafts the reply/action. A human agent reviews and sends it. Wilmo surfaces *how much a human edits* each category as a visible metric. |
| **Human Review** | AI never sends on its own for this category. A human always reviews first. |

Note: their third tier is called **"Human Review,"** not "Blocked." The brief's assumed name doesn't match what's publicly findable — worth using accurate terminology if this is ever compared to a competitor publicly.

**Promotion mechanics:**
- Every new category **starts in Copilot** — nothing launches directly on Autopilot.
- Promotion is **category-level, not account-level** ("every ticket category can be in one of three modes").
- Promotion is **merchant-initiated**, gated by **performance data from that merchant's own historical tickets** ("you promote categories to Autopilot only when performance data from your own tickets says it is ready. No guesswork").
- No explicit numeric confidence threshold or minimum sample size was found in public search results — may exist on the page but isn't surfaced in a way search indexes.

**Example categories mentioned:** order tracking/WISMO, cancellations, refunds, returns, order edits (address/quantity changes before shipping), product/stock questions, size exchanges, product questions and complaints.

**Metrics claimed:** the only concrete, mechanism-level metric found is the **per-category human-edit-rate visibility** during Copilot. Case-study numbers (see below) are the other concrete data point. A vague "70%+ automation" style claim also surfaced but without denominator or timeframe — treat as marketing copy, not a defined metric. **No CSAT claim was found anywhere**, despite a dedicated search for it.

**Case studies (three identified, all Danish/EU merchants):**

| Merchant | Result |
|---|---|
| Viamaja | 78% of tickets on Autopilot in 26 days; 1.2M DKK/yr saved; "four FTE → one and a half" |
| Oversized Lifting Club | 84% on Autopilot in 36 days; 231k DKK/yr saved |
| Havehandel | 85% on Autopilot (no timeframe found) |

**Guardrails claimed:** the tier model itself is the stated safety mechanism, plus "full control over what runs on autopilot" and a mention of "simulation testing" before a category goes live (no further detail surfaced). **No kill-switch, pause, or rollback mechanism was found in search results — this is an open question, not a confirmed absence.**

**Positioning:** "AI agents that resolve ecommerce support tickets on autopilot. The Gorgias and Zendesk alternative built for AI, not chatbots." Core differentiation claims: AI-native architecture (not a legacy helpdesk with AI bolted on), action-taking over reply-generating, "knowledge capture" from the merchant's team as a stated learning mechanism, and merchant-controlled rollout as a trust device. Company context (from press, not the product pages): founded 2025, ~6 months old at last funding report, ~$190K raised at an $8M valuation, ARR reported growing €10K→€85K over that period — a genuinely early-stage competitor, useful for calibrating how much stock to put in their case-study pace.

---

## 2. What we should copy conceptually

1. **Category-level automation, not a global switch.** This matches the task's own hard requirement and is independently validated by Wilmo's own design — they don't do a single AI ON/OFF either.
2. **New categories default to human-reviewed, always.** Nothing should ever start on Autopilot for a brand-new brand or a brand-new category.
3. **Promotion is merchant-initiated and grounded in that merchant's own historical data**, not a generic "AI is 95% accurate" vendor claim. This is directly compatible with tResolv's existing `autopilot_readiness` pattern (`backend/src/api/routes/v2_brands.py:290-303`), which already refuses to show a number below a minimum sample size (see §5) — that instinct is correct and should be generalized, not replaced.
4. **Surfacing the human-edit signal as its own dimension**, distinct from a flat approve/reject binary. This is a real, valuable idea tResolv does **not** currently implement (see §5's readiness audit — there is no "AI proposed vs. human corrected" tracking for any action type except a bare refund-amount override that isn't even persisted as a diff). Worth adding.
5. **Framing outcomes as % on Autopilot + time-to-get-there + $ saved.** Good narrative shape for tResolv's own future case studies — but only ever with tResolv's real, computed numbers, never Wilmo's pace as a target to hit.
6. **Action-taking as the core value proposition**, not just replying. This already matches tResolv's direction and the deep guardrail infrastructure already built (§7) — validates that the product bet is sound, not that it needs to change direction.

## 3. What we should NOT copy

1. **Don't publish unverifiable percentage claims.** Wilmo's public "70%+ automation" framing has no visible denominator or timeframe. The task's own instruction — never invent or show a number without real data behind it — already rules this out; keep enforcing it even in future marketing, not just in-app UI.
2. **Don't chase their rollout pace.** 26–36 days to 78–85% Autopilot reflects an early-stage startup's incentive to prove traction fast on their own case studies, not necessarily a safe reference speed for tResolv merchants who may carry higher-AOV catalogs, apparel sizing complexity, or thinner margins for a wrong refund. Set thresholds from tResolv's own risk tolerance and real data, not competitor envy.
3. **Don't build a single global toggle.** Already ruled out by the task; also not what Wilmo does.
4. **Don't drop CSAT.** tResolv already collects real per-ticket CSAT (`chat_feedback` table, hardened send logic — see §5) that Wilmo doesn't appear to showcase publicly. This is a keepable, strengthenable differentiator, not something to deprioritize because a competitor doesn't lead with it.
5. **Don't adopt silent/automatic promotion.** Wilmo's own language says "you promote" — merchant-gated. tResolv should match or exceed this with the task's explicit Stage 4 "Review evidence / Enable Autopilot" consent screen, never an automatic threshold-crossing flip.
6. **Don't copy their tier names uncritically without copying the substance.** "Autopilot"/"Copilot" are now generic enough industry terms to reuse, but the *substance* — real per-category evidence, real audit trail, real guardrails — is what tResolv can credibly claim today that an early-stage competitor's public marketing doesn't demonstrably prove.

---

## 4. Proposed tResolv automation modes

Three modes, per category, matching the task's requirement and independently validated by Wilmo's pattern:

| Mode | Behavior | Current tResolv reality |
|---|---|---|
| **Blocked** | Luna may still triage/reply informationally, but never proposes or executes a sensitive action for this category. Always routed to a human for the action itself. | Effectively already true for anything with no implemented Shopify mutation (line-item edits, customer profile changes — see §9). |
| **Copilot** | Luna proposes an action; a human must approve before Shopify is touched. | **This is the only mode that functionally exists in the live code path today.** `auto_approve_threshold` exists as a brand column (`backend/src/api/routes/v2_brands.py` `SAFE_COLUMNS`) but is not read anywhere in `actions_service.py`'s live approval flow — it's a dormant field, not a working dial. Every action type currently requires a human click. |
| **Autopilot** | For an eligible ticket in this category, Luna executes the guarded Shopify action automatically, with the exact same backend gates Copilot already uses, and replies with a factual confirmation. | **Does not exist yet.** Building it is genuinely greenfield work, not "flip a switch that's already there" — important framing for scoping estimates. |

**Critical requirement carried forward unchanged:** mode is set **per category, per brand**, merchant-controlled, never a single account-wide flag. This should be modeled as a new settings table (e.g., one row per `brand_id` + `category`) rather than a single JSON blob, so kill-switch and audit logic can target one category cleanly (see §8).

---

## 5. The training → confidence → Autopilot journey

### Stage 1 — Observe
Luna handles the conversation, no sensitive action is proposed. This is close to tResolv's actual current baseline for any new category: the LLM function-calling surface (`backend/src/services/tools.py`) exposes **zero write tools** — every registered tool is read-only (`get_order_status`, `get_orders_by_email`, `get_shipping_status`, `get_inventory_status`, `get_product_recommendations`, `escalate_ticket`, etc.). Luna has never had the ability to directly cause a Shopify write; "Observe" for a new category is mostly a labeling/reporting concept, not new mechanism.

### Stage 2 — Copilot (what exists today, plus what to add)

**Already recorded per action** (`actions` table, `backend/migrations/004_saas_clean_setup.sql:56-84`, columns confirmed live in `actions_service.py`):
- Approved / rejected / executed / failed status (`ActionStatus` enum, `actions_service.py:36-41`)
- Rejection reason (free text, `actions_service.py:834`)
- Shopify execution result — structured, on success (`execution_result` jsonb) and on failure (`error_message` + `execution_result.error`)
- Policy evidence — a bounded (~800 char) excerpt of the real policy text that justified/blocked the action (`return_actions_integration.py:43-51`), shown in the dashboard's "View policy evidence" expander (`dashboard/src/pages/Actions.jsx:77`)
- CSAT — real, hardened, deterministic (no LLM in the send path), 30-90 min post-resolution, 2+ message minimum, 30-day per-customer-per-brand cooldown, HMAC-signed star links (`backend/src/channels/email_poller.py:502-562`) — but linked only to a **ticket**, not to an **action or category**. The join path (`chat_feedback.ticket_id → tickets.id → actions.ticket_id → actions.action_type`) exists in the data model but nothing computes it today.

**Genuinely missing today (net-new work required):**
- **Was it edited before approval?** No such flag exists for any action type. The only human-editable field anywhere in the approval UI is an optional refund-amount override (`actions_service.py:363,383-390`; `Actions.jsx:83,97-104`) — and it isn't persisted as a distinguishable "AI proposed $X, human approved $Y" diff, it just becomes the executed amount. `cancel_order`, `change_address`, `exchange`, `reship`, `restore_order` have **no editable fields at all** in the approval UI. Recommend: add an `original_extracted_data` snapshot alongside the eventually-approved values, and a computed `was_edited` boolean, for every action type — this is the single most valuable missing signal for a Wilmo-style readiness story.
- **Structured rejection-reason taxonomy.** Today it's free text only. A small fixed enum (e.g., `wrong_amount | against_policy | duplicate_request | insufficient_evidence | other`) would make rejection data usable for readiness scoring and policy-tuning, not just human-readable in a card.
- **Structured escalation-cause taxonomy.** The dominant escalation path (`customer_success_agent.py:986-994`) is a bare `confidence_score < 70 or risk_level == "high"` check — the model's own JSON schema doesn't even ask for a reason (`customer_success_agent.py:1198-1208`), so `tickets.escalation_reason` is **null for most real escalations** today; it's populated only by a handful of hardcoded safety-backstop and billing strings. This is a real gap for both the "Why Luna Escalated" UX and for any future routing/policy-config signal.

### Stage 3 — Readiness (what exists, what must be fixed before it's trustworthy)

`GET /{brand_id}/analytics`'s `autopilot_readiness` field (`v2_brands.py:290-303`) is real, honest, and a good pattern — but narrow and has one correctness bug:

- **Scoped to exactly one action type: `cancel_order`**, hardcoded (`v2_brands.py:294`). Not generalized to `refund`/`exchange`/`change_address` etc.
- **All-time, not windowed** — an old approval streak counts identically to a fresh one; no recency weighting.
- **Formula:** `approval_rate = 100 * executed / (executed + rejected)`, minimum sample **5** (`_AUTOPILOT_MIN_SAMPLE`, line 238), returns `null` (card omitted) below that — this "don't show a fake number" instinct is correct and should be preserved when generalized.
- **Bug to fix before this number ever gates real automation:** `status = 'failed'` (a genuine Shopify execution failure, distinct from human rejection) is **excluded from the denominator entirely**. A spike in real Shopify execution failures for a category currently would not move this number at all. Must be fixed to count `failed` as a negative signal before any readiness score is used to unlock Autopilot.
- **CSAT is computed brand-wide only** (`v2_brands.py:288`), not joined to category — despite the join path existing in the schema (see Stage 2 above).

**Recommended generalized readiness computation, per category:**
```
approval_rate      = executed / (executed + rejected)          [existing pattern, fix denominator]
execution_success   = executed / (executed + failed)            [new — Shopify-side reliability, distinct from human trust]
edit_rate           = was_edited / executed                     [new — requires Stage 2's new tracking]
category_csat       = avg(chat_feedback.rating_stars) joined via tickets → actions.action_type  [new — join already possible, just uncomputed]
sample_size         = executed + rejected                       [existing pattern]
```
Keep the existing "don't show it below a minimum sample" behavior. A recency-weighted version (e.g. trailing 90 days weighted higher) is a reasonable Phase-2 enhancement, not required for a first version.

### Stage 4 — Merchant approval (net-new UI, backed by real fields above)
Build exactly the card shape the task specifies — "Cancellation is ready for Autopilot… 40 approved without changes, 2 required corrections… [Review evidence] [Enable Autopilot]" — using the real `edit_rate`, `approval_rate`, and a handful of real representative examples (with policy evidence excerpts, which already exist and are genuine). Enabling writes an explicit per-category setting; it is never inferred or auto-flipped.

### Stage 5 — Autopilot (the good news: most of the hard guardrail plumbing already exists)
The task's 9-step flow maps almost directly onto code that's already built and audited for the three financially-critical action types (`refund`, `cancel_order`, `exchange` — `_AUDITED_ACTION_TYPES`, `actions_service.py:24`):

1. Understand request → existing LLM intent classification (unchanged)
2. Verify customer/order ownership → existing sender-email match in `check_return_eligibility()` (`actions_manager.py:146-375`)
3. Fetch live Shopify state → **already happens on every mutation call** — each `ShopifyClient` mutation method (`cancel_order`, `process_refund`, `update_shipping_address`) re-fetches the order live before acting (`shopify_service.py:564,687,747`), never trusts a staged snapshot
4. Check merchant policy → existing `check_return_eligibility()` deterministic gauntlet, unresolved policy fails toward human review (`actions_manager.py:332-356`)
5. Check automation guardrails → **new**: category mode = Autopilot, kill switch not engaged, readiness score above merchant-approved threshold, action-specific limit not exceeded (§7)
6. Execute the Shopify action → **already exists and is audited** — the exact same `ShopifyClient` methods and `actions_service.approve_action()` execution path Copilot uses today
7. Verify Shopify success → **already exists** — each mutation checks Shopify's response fields, not just HTTP status (e.g. `cancel_order`'s `cancelled_at` check, `process_refund`'s transaction-status check)
8. Reply to customer → **already exists**, plus the existing `_enforce_no_unconfirmed_action_success()` backstop (`customer_success_agent.py:229-260`) that force-escalates if the model ever claims success before it's confirmed — **this exact same backstop must gate the Autopilot path too**, not just the human-approval-pending path.
9. Record the full audit trail → **already exists** — append-only `financial_action_audit_log` (DB-grant-revoked from UPDATE/DELETE, `migrations/027...sql:34-40`) plus `action_logs`.

**The real missing piece is step 5** — the automation-guardrail gate — plus the trigger being automatic instead of a human click, plus consolidating the approval layer (see §7's critical finding) so Autopilot logic is built on the one audited path, not the fragmented set of surfaces that exists today.

### Human-team-training signals (task item 10) — what should feed what
No fine-tuning is proposed anywhere in this plan, consistent with the task's constraint. Deterministic scoring and configuration only:

| Signal | Should feed |
|---|---|
| Approval / rejection / edit rate, execution success (per category) | **Readiness score** (Stage 3) |
| Structured escalation-cause taxonomy (once built) | **Routing** — repeated causes (e.g. "ambiguous request") could flag a category for prompt review |
| Repeated rejection reasons citing policy (e.g. "outside return window") | **Policy configuration** — surfaced as a merchant-facing insight ("12 rejections cited your 30-day window — consider extending it?"), never auto-changed |
| Edit deltas + rejection reasons | **Response quality** — the concrete "what did Luna get wrong" signal for prompt/policy iteration |
| The Stage-3 readiness gate itself | **Future automation eligibility** |

---

## 6. Recommended default modes by ticket category

Adjusted from the task's example table using tResolv's *actual* implementation capability (a category can't safely be offered as "Copilot," let alone "Autopilot," if the underlying Shopify mutation doesn't exist yet — see §9):

| Category | Recommended default | Why |
|---|---|---|
| WISMO / order status / shipping inquiry | **Autopilot** | Read-only today already (`tools.py` exposes only reads) — zero Shopify write risk. Safest possible starting category, matches Wilmo's own default. |
| Order questions (non-status) | **Autopilot** | Read-only. |
| Product questions | **Autopilot for informational answers** | Read-only and genuinely well-grounded (§10) — but fix the two conversational gaps (pronoun follow-ups, variant/color swap) first so "Autopilot" doesn't mean silent dead-ends. |
| Cancellation | **Copilot** → first real Autopilot candidate | Fully guarded, audited execution path already exists (`_AUDITED_ACTION_TYPES`); simplest single-mutation action with a clean success/fail signal. |
| Refund | **Copilot**, longer observation window than cancellation | Directly monetary and irreversible; also the *only* category with any human-edit surface today (amount override) — good first place to add real edit-tracking. |
| Return | **Copilot** | Resolves to a refund/exchange action depending on flow — same caution as those. |
| Exchange | **Copilot** → second-wave Autopilot candidate | Fully guarded execution path exists (`create_exchange_draft_order`), but multi-step (draft order → complete/invoice) and balance-due exchanges add complexity — keep balance-due sub-case Copilot even after the category graduates. |
| Address change (pre-fulfillment) | **Copilot** | Implemented and guarded, but see §9a — post-fulfillment requests in this category must never attempt the same action. |
| Address change (post-fulfillment) | **Blocked** | Shopify's own address-update mechanics don't meaningfully affect an already-fulfilled shipment — must route to human judgment (carrier contact / cancel+reship decision), not a silent no-op Shopify call. |
| Quantity / line-item edits | **Blocked (not yet available)** | **Not implemented at all** in the current Shopify integration (confirmed: zero order-edit endpoint usage anywhere in `shopify_service.py`). Don't expose as a configurable category until built. |
| Customer profile changes (name/email/default address) | **Blocked** | Not implemented; also carries cross-order blast radius fundamentally different from an order-scoped change — needs its own design, see §9a. |
| High-risk / large-dollar-value actions (any category, above a merchant-set $ threshold) | **Blocked, cutting across category mode** | An action-specific limit that overrides an otherwise-Autopilot category — a $5 refund and a $500 refund shouldn't share one automation decision. |

---

## 7. Autopilot guardrails

Every guardrail the task lists, mapped to what's real today vs. genuinely missing:

| Guardrail | Status | Detail |
|---|---|---|
| Tenant ownership | **Exists**, with one gap | `actions_service.get_action()` filters by `tenant_id`+`brand_id` (`actions_service.py:343-354`); `v2_actions.py:389` checks brand ownership. **`/api/brand-actions/approve/{id}` (`brand_actions.py`) has no authentication or ownership check of any kind and is live in production** (`main.py:313-317`). This must be closed before Autopilot ships — not because the task asked to re-audit security, but because this research surfaced it as directly relevant to whether "the backend is authoritative" is actually true everywhere. |
| Shopify state verification | **Exists** | Every mutation re-fetches the order live before acting (§5, Stage 5 step 3). |
| Merchant policy | **Exists** | `check_return_eligibility()`, deterministic, produces citable reason strings, fails toward human review on ambiguity. |
| Action-specific limits | **Missing — net new** | `auto_approve_threshold` column exists on `brands` but is unused in the live path. Needs generalizing to per-category, per-action-type limits (a dollar cap on refunds is a different knob than a volume cap on cancellations). |
| Entitlement | **Partially exists** | Plan/usage limits are checked (`check_limit(..., "shopify_actions", ...)`, `v2_tickets.py:826-829`). **Missing:** verifying the brand's live Shopify OAuth grant actually includes `write_orders` before allowing a category into Autopilot — the code's *default* requested scope is `read_products,write_products` only (`shopify_oauth.py:94`), with no `write_orders` in the default env var. This should be an explicit pre-flight check, not something discovered as a runtime 403 during a customer's ticket. |
| Customer/order verification | **Exists** | Sender-email match check inside the eligibility gauntlet. |
| Idempotency | **Exists, but only on 3 of 5 approval surfaces** | Atomic conditional claim + `Idempotency-Key` unique-indexed in `financial_action_audit_log` — present in `actions_service.py`, `saas_actions.py`, `v2_tickets.py`; **absent** in `v2_actions.py`'s own approve route and in `multi_brand_actions.py`. Autopilot must build exclusively on the audited path. |
| Existing action lifecycle protections | **Exists** | Status state machine with atomic transitions (the reject-race-condition fix already applied here). |
| Audit logging | **Exists, and is a real strength** | Append-only at the DB grant level (`REVOKE UPDATE, DELETE, TRUNCATE`), RLS-blocked from ordinary read access. |
| Luna's model output must never itself authorize an action | **Already true today** | Zero write tools registered for LLM function-calling; a deterministic regex backstop (`_enforce_no_unconfirmed_action_success`) already force-escalates if the model claims a completed action anyway. Autopilot's execution trigger must be the same deterministic backend gate reading structured `extracted_data` + the automation settings — never "trust Luna said it's fine." |

**Pre-existing findings surfaced by this research that block a trustworthy Autopilot regardless of feature work:**
1. **`/api/brand-actions/approve/{id}` has no auth check** — close or retire this surface.
2. **Three parallel action-tracking systems exist** (`actions`, legacy `pending_actions`, `brand_actions`) with inconsistent guardrail coverage. Autopilot logic must be built exclusively on the `actions`/`actions_service.py` path; the other two should be consolidated away or explicitly frozen before Autopilot ships, so a future contributor can't accidentally add an Autopilot trigger on the wrong table.
3. **Default OAuth scope doesn't request `write_orders`** in code — needs verifying against the actual live Shopify Partner app config, and an explicit scope-sufficiency check added regardless.

---

## 8. Kill-switch design

**Global kill switch:** a single boolean/timestamp (e.g. `brands.autopilot_paused_at`) checked as the very first gate in the Stage-5 execution path (§5). Flipping it reverts every category to Copilot behavior instantly — no other code path changes, since Copilot's human-approval flow never goes away, it's just what happens when the Autopilot gate fails. This is intentionally the cheapest possible circuit breaker: one column, one check, no new execution logic.

**Category-level rollback:** the per-category `mode` field flips `Autopilot → Copilot` independently (e.g. "Pause cancellations" without touching WISMO). In-flight already-staged `pending` actions are unaffected either way — they were always waiting on human approval in Copilot, so a mode flip doesn't strand anything mid-execution. Log who/when/why for every mode change, reusing the existing `action_logs` audit pattern.

**Recommended Phase-2 addition (not required for MVP):** an automatic-pause trigger — once the Stage-3 fix makes Shopify execution failures visible in the readiness number (§5), a rolling-window failure-rate spike for a category could auto-demote it back to Copilot and notify the merchant, as a safety net beyond the manual kill switch. Explicitly scope this as a later phase; the manual kill switch and category rollback are the MVP requirement.

---

## 9. Shopify mutation capability matrix

All operations below are genuinely supported by Shopify's own Admin API — the gaps identified are entirely in what tResolv has chosen to build, never a case of Shopify lacking a capability.

| Capability | Shopify API | Current tResolv integration | Required scope | Can safely automate? | Conditions |
|---|---|---|---|---|---|
| Cancel order | REST `POST orders/{id}/cancel.json` | **Implemented** — `ShopifyClient.cancel_order()`, `shopify_service.py:670-730` | `write_orders` | **Yes, with conditions** | Not already cancelled/fulfilled per policy; within return window; execute only via the audited `actions_service.py` path. |
| Refund | REST `POST orders/{id}/refunds.json` | **Implemented** — `process_refund()`, `shopify_service.py:545-668` | `write_orders` | **Yes, with conditions** | Amount at or below policy/threshold cap; no existing refund; within window. Note: Luna never proposes a partial dollar figure today (`extracted_data.amount` is never AI-set) — Autopilot refunds should stay limited to full policy-eligible amounts until edit-tracking (§5) matures. |
| Shipping address change | REST `PUT orders/{id}.json` (shipping_address field) | **Implemented** — `update_shipping_address()`, `shopify_service.py:732-819` | `write_orders` | **Conditionally — pre-fulfillment only** | Must check `fulfillment_status`; see §9a for why post-fulfillment is fundamentally different, not just riskier. |
| Reopen/restore a cancelled order | REST `POST orders/{id}/reopen.json` | **Implemented** — `reopen_order()`, backs `restore_order` action type | `write_orders` | **No — keep manual/Blocked regardless of category maturity** | Unusual corrective operation, not a customer self-serve flow. |
| Exchange (via draft order) | REST `POST draft_orders.json`, then `.../complete.json` or `.../send_invoice.json` | **Implemented** — `create_exchange_draft_order()`, `shopify_service.py:847-960` | `write_draft_orders`, `write_orders` | **Yes, with conditions** | Same-or-lower-value exchanges are the safer Autopilot case; balance-due exchanges (customer must pay more) should stay Copilot even after the category graduates. |
| Order line-item / quantity edit | GraphQL `orderEditBegin` / `orderEditAddVariant` / `orderEditCommit` | **Not implemented** — zero references anywhere in `shopify_service.py` | `write_order_edits` (not currently requested) | **No — not built** | Do not present as a configurable category until implemented and independently audited. |
| Customer name/email/default-address change | GraphQL `customerUpdate` / REST `PUT customers/{id}.json` | **Not implemented** — no customer-mutation code exists anywhere | `write_customers` | **No — not built, and needs deliberate design, not just an endpoint** | See §9a. |
| Fulfillment cancel/hold | GraphQL `fulfillmentOrderCancel` / hold mutations | **Not implemented** | `write_fulfillments` (not requested) | **No current use case identified** | Skip unless a specific ticket category needs it. |

### 9a. Address / name / email changes — deep dive (task item 8)

This deserves its own careful read because the two objects involved are easy to conflate and the consequences of conflating them are large.

- **What actually owns the data:** `order.shipping_address` (and `order.billing_address`) are **snapshotted at order-creation time** — they are copies, not live references to the customer's profile. `customer.default_address`, `customer.email`, `customer.first_name`/`last_name` are a **separate record** that governs every *future* order and account-level communication for that person.
- **Can it change after order creation?** Yes — order shipping address, pre-fulfillment, via the implemented `update_shipping_address()`.
- **Can it change after fulfillment?** Practically no, in any way that matters: Shopify either blocks the update or the change has zero real-world effect, since the shipping label and carrier already have the old address — editing the Shopify record afterward doesn't reroute a package in transit. **This must be a branch in the flow, not a risk tier:** pre-fulfillment → real address-change action; post-fulfillment → escalate to a human with "package may already be in transit" framing, never a silent no-op Shopify call that looks like it worked.
- **Does changing customer-profile data differ from changing order shipping address?** Fundamentally yes — a customer-profile change affects **every future order and communication** for that person; an order-scoped change affects **only that one order**. These must be two separate, separately-scoped actions, never one generic "edit" tool.
- **Could changing customer data accidentally affect future orders?** Yes — this is the actual risk, not a hypothetical: if a "fix the address typo" flow edits `customer.default_address` instead of `order.shipping_address`, it silently redirects every future order for that person. Recommend tResolv default to **order-scoped changes only**, and treat any customer-profile change (email/name/default address) as a distinct, explicitly-labeled, always-**Blocked** action — never Autopilot, likely never even unassisted Copilot without a stronger identity-verification step than a sender-email match, since an email change itself affects login/marketing/account access.
- **Verification required:** order-address changes can reasonably reuse the existing sender-email-match check. Customer-profile changes should require a stronger, merchant-configured verification step before this is ever automated at all — this is exactly why it's recommended Blocked in §6, not just "Copilot with extra caution."
- **Bottom line, restated per the task's explicit instruction:** build/keep these as separate tools (`update_order_shipping_address` vs. a hypothetical future `update_customer_profile`), never a single "edit order" or "edit customer" catch-all.

---

## 10. Product recommendation audit

The capability is real and already wired into live conversation — not a dead endpoint behind an unused search API. Verified end-to-end trace: keyword-triggered (`customer_success_agent.py:531-742`) → live Shopify product fetch (`shopify_service.py:476-543`, fresh at call time, no stale cache) → deterministic rule-based scoring (`tools.py:454-479`: +3 exact `product_type` match, +N tag overlap, +1 weak vendor signal) → grounded into the LLM prompt with an anti-hallucination instruction → a deterministic post-hoc guard (`_enforce_no_ungrounded_recommendation`, `customer_success_agent.py:336-357`) that strips anything the model invents beyond the tool's actual results.

**What it has, genuinely:** live title, description, variants (correctly option-name-aware, not hardcoded to "size"/"color"), live price, live inventory (correctly treats untracked inventory as always-available rather than falsely flagging it out of stock), image URLs, product page URLs, tags, vendor. Out-of-stock items are **flagged, not silently filtered** — a customer can still be told "we have X but it's currently out of stock." Extensive test coverage (`test_product_recommendations.py`, `test_product_discovery.py`) explicitly proves an "honest-degradation-first" contract: ambiguous names, no-match, no-scoring-candidates, Shopify API failure, and missing credentials all have named, tested, non-guessing responses.

**What it deliberately does not have (by design, not oversight):** no semantic/embedding matching (pure rule-based, explicitly documented as such), and **no true complementary/cross-sell data** — a "what goes with this" query gets an honest, hard-coded refusal (`tools.py:414-423`, "I don't have reliable data on what's specifically meant to be paired with that item") rather than a fabricated pairing, and a deterministic guard strips it out even if the model tries anyway. This honesty is a genuine asset, not a gap to "fix" by inventing pairing data.

**Real, unaddressed gaps that affect how good "Autopilot for product questions" would actually feel to a customer:**
1. **No cross-turn pronoun resolution.** "What else do you have like *this*?" only works if a concrete product name is given — a bare "this"/"that" is deliberately left unresolved (confirmed by test and code comment, not a silent bug) and the message falls through to a generic, ungrounded reply.
2. **No dedicated "same product, different variant/color" path.** "Do you have this in a different color?" doesn't match any recommendation/discovery trigger — it falls into a generic inventory-lookup regex that will usually fail to find a literal product titled that phrase.
3. **No product-card UI anywhere** (widget or dashboard) — recommendations are narrated as plain text only; image URLs and prices are fetched and passed to the model but never rendered as a visual card.

**Recommendation:** fix #1 and #2 before leaning on "Product questions: Autopilot" as a polished customer promise — the underlying data and safety story are strong, but these two conversational dead-ends are the most customer-visible weak points in an otherwise well-built capability. Neither is a Shopify-write-risk issue (this category never touches actions/guardrails), so they're pure product-quality work, not gated by anything in §7–§9.

---

## 11. Customer experience during Copilot (and other modes)

The core rule, unconditionally: **the customer must never be left believing "someone will reply soon" when the merchant may not respond until tomorrow, and Luna must never claim an action happened before Shopify has actually confirmed it** (the latter is already enforced today by `_enforce_no_unconfirmed_action_success` — this exact backstop must also gate the future Autopilot path, not just today's human-approval-pending path).

| Situation | Message shape |
|---|---|
| **Copilot, business hours** | Concrete and time-bound: "I've forwarded this cancellation request to our team — they typically review within [real configured business-hours SLA]." The number in brackets must come from an actual configured SLA setting, never an invented per-ticket guess. |
| **Copilot, outside business hours** | Must not read identically to the in-hours message: "Our team is outside business hours right now (next available: [real configured time]) — I've flagged this for review as soon as they're back." |
| **Autopilot, action completed** | Factual and specific, only once Shopify has confirmed: "Your order has been cancelled and a refund of $X was issued to your original payment method." Never "should be done soon" for something that already, verifiably, happened. |
| **Blocked / high-risk request** | Transparent that this always needs a person — don't imply Luna "looked into it" if the routing decision was purely category-based: "This needs a member of our team to review directly — I've sent it to them now." |

**Cross-cutting rule:** never say "someone will reply soon" without grounding it to a real configured SLA value. If no SLA is configured for a brand, use an honest, ungrounded-but-not-falsely-urgent phrase ("our team will follow up") rather than inventing a specific timeframe.

---

## 12. Strongest tResolv differentiators

Of the task's candidate list, three are both realistically buildable on what already exists and genuinely hard for a thin LLM-wrapper competitor to match quickly:

1. **Category-level, merchant-controlled, data-backed graduation to Autopilot.** This isn't just a UX pattern — it's provably backed by real guardrail infrastructure that already exists in this codebase (live state re-verification, deterministic policy checks, append-only audit logging, idempotency). A merchant evaluating a small AI vendor can be shown, not just told, exactly why a category is ready.
2. **Transparent, auditable action history paired with policy-grounded actions.** The `financial_action_audit_log` is append-only at the database grant level (not just an application convention), and the policy-evidence excerpts shown for "why Luna did/didn't do this" are real citable text, not an LLM's paraphrase of itself. This is provable trust, which matters more than marketing copy at the trust-building stage most Shopify merchants are at with AI support tools.
3. **Real Shopify execution paired with honest failure behavior everywhere, verified end to end.** No LLM write path exists anywhere; no fabricated product pairings; no false "action completed" claims (enforced by a specific regex backstop, not just a prompt instruction). The product's core credibility bet — it never lies about what it did or didn't do — is measurable and demonstrable to a skeptical merchant, which is a durable position against cheaper competitors optimizing for a flashier demo rather than this level of defensive engineering.

*(Deprioritized for a "pick 3" answer, but worth keeping as supporting features, not the headline: customer feedback loop, rollback/kill-switch, measurable readiness — all real, all good, but they're the mechanism behind differentiator #1, not separate wedges on their own.)*

---

## 13. Recommended implementation phases

**Phase 0 — Prerequisites (before any Autopilot-specific code):**
- Close or retire the unauthenticated `/api/brand-actions/approve/{id}` surface; consolidate away from the parallel `pending_actions`/`brand_actions` systems so there's one audited approval path.
- Verify the brand's actual live Shopify OAuth grant includes `write_orders` (not just the code default) and add an explicit scope-sufficiency check.
- Fix the readiness-computation bug that excludes `status='failed'` from the approval-rate denominator.
- Add `original_extracted_data` + `was_edited` tracking to the `actions` table for all action types (currently only a bare, non-persisted refund-amount override exists).

**Phase 1 — Readiness visibility (still Copilot-only execution, no new automation):**
- Generalize `autopilot_readiness` from hardcoded `cancel_order`-only to per-category, using the fixed denominator and the new edit-rate signal.
- Compute per-category CSAT via the existing (but currently unused) `chat_feedback → tickets → actions` join.
- Build the Stage 4 "ready for Autopilot / Review evidence / Enable Autopilot" merchant-facing card.

**Phase 2 — First real Autopilot (narrowest safe scope):**
- Build the category-level `automation_settings` table (mode per brand+category) and the global + category kill switches.
- Build the Stage-5 automation-guardrail gate (§5 step 5) and wire it in front of the *existing, already-audited* execution code path — reuse, don't rebuild, the Shopify call + verification + audit-log logic.
- Ship for the lowest-risk categories first: read-only categories (WISMO, order/product questions — already effectively safe) and `cancel_order` (simplest guarded mutation with a clean success/fail signal), once real per-category data clears the readiness bar.

**Phase 3 — Expand and harden:**
- Extend Autopilot eligibility to `refund` and `exchange` once edit-tracking and the escalation-cause taxonomy are live and enough real data has accumulated.
- Build the Phase-2-grade auto-pause-on-failure-spike safety net (§8).
- Fix the two product-recommendation conversational gaps (§10) so "Autopilot for product questions" doesn't produce silent dead-ends.

**Phase 4 — Explicitly out of scope for now:**
- Line-item/quantity edits and customer-profile changes both require net-new Shopify integration work (new scopes, new mutation code) *and* the extra identity-verification design from §9a. Do not schedule either until there's a validated merchant demand signal — building the mutation without the design work around it is exactly the "dangerous edit-order catch-all" the task warned against.

---

*No code, migrations, or dependencies were added as part of this research task, per the task's instructions. This document is a planning artifact — a future `/sp.specify` pass on the categories greenlit from this plan would be the next step if the team wants to move toward implementation.*
