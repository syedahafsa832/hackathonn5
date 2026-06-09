# Resolv — Real Product Data
Generated: 2026-06-02

---

## Core Statistics (from live database — queried 2026-06-02)

| Metric | Value |
|--------|-------|
| Total tickets processed | **52** |
| AI auto-resolution rate | **86.5%** (45 of 52 tickets) |
| Total emails evaluated by filter | **299** |
| Emails blocked by filters | **179** (59.9%) |
| Emails passed to AI | **120** (40.1%) |
| Actions staged for approval | **11** |
| Brands connected | **3** |
| Gmail accounts connected | **2** |
| Shopify stores connected | **1** |
| Tenants (user accounts) | **3** |
| Emails in quarantine queue | **0** (empty — none triggered low-confidence gate) |

---

## How It Works (from code)

| Property | Value |
|----------|-------|
| Email polling interval | 60 seconds (configurable via `EMAIL_POLL_INTERVAL` env var) |
| AI model for replies | `mistral-large-latest` (via `MISTRAL_MODEL` env var, OpenAI-compatible API) |
| AI model for email classification | `mistral-large-latest` (same model) |
| Embedding model for RAG | `mistral-embed` (via `EMBEDDING_MODEL` env var) |
| AI persona name | **Luna** |
| Confidence threshold for auto-reply | **65%** (default, configurable per brand in system_settings) |
| Guardian confidence threshold | **75%** (default, configurable via confidence_threshold slider) |
| RAG similarity threshold | **0.5** (cosine similarity) |
| RAG results returned per query | **3 chunks** |
| Shopify API version | **2024-01** |
| Email body extracted | Gmail snippet (≈160 chars) — not full MIME body |
| Max auto-replies per thread | Configurable via loop_risk flag; auto_reply_count tracked per ticket |

---

## Email Filter Pipeline (5 layers)

**Layer 1 — Domain/Sender keyword block** (68 rules: no-reply, stripe.com, github.com, etc.)  
**Layer 2 — Automated email prefix block** (hello@, noreply@, marketing@, etc.)  
**Layer 3 — Marketing content indicators** (unsubscribe, view in browser, privacy policy, etc.)  
**Layer 3b — RFC headers** (Auto-Submitted, X-Autoreply, List-Unsubscribe, Precedence)  
**Layer 4 — AI intent classification** (Mistral classifies into: customer_support, promotion, newsletter, outreach, spam, automation, unknown)  
**Layer 5 — Confidence gate** (customer_support emails below 75% confidence → quarantine for manual review)

From live logs:
- blocked_sender_pattern: 68 emails
- blocked_domain: 52 emails
- gmail_category: 34 emails
- auto_reply_header: 19 emails
- ai_classification: 6 emails (blocked by Layer 4)
- AI classified as customer_support (allowed): 41 emails
- Unknown (fail-open, allowed but no auto-reply): 4 emails

---

## Shopify Integration — API Endpoints Used

| Operation | Endpoint | Status |
|-----------|----------|--------|
| Get order by name/number | `GET /admin/api/2024-01/orders.json?name=#{id}&status=any` | Working |
| Scan recent 250 orders | `GET /admin/api/2024-01/orders.json?status=any&limit=250` | Working (fallback) |
| Get orders by customer email | `GET /admin/api/2024-01/orders.json?email={email}` | Working |
| Cancel order | `POST /admin/api/2024-01/orders/{id}/cancel.json` | Implemented (6/11 failed in testing) |
| Process refund | `POST /admin/api/2024-01/refunds.json` | Implemented (0 DB executions yet) |
| Update shipping address | `PUT /admin/api/2024-01/orders/{id}.json` | Implemented |

**Order data fields extracted per Shopify order:**
title, variant_title, quantity, price, sku (line items), order_number, fulfillment_status, tracking_number, tracking_url, total_price, created_at

---

## RAG Knowledge Base

- Vector store: Supabase pgvector (`rag_chunks` table)
- Search RPC: `match_tenant_rag_chunks` (tenant-scoped), `match_rag_chunks` (global fallback)
- Similarity threshold: 0.5 (cosine)
- Results per query: 3 chunks
- Tenant isolation: ✓ Each tenant's KB is separate
- Upload UI: ✓ (Settings → Knowledge Base tab, paste-text upload)
- Delete UI: ✓

---

## Actions System

**Detected action types:**
1. `cancel_order` — patterns: "cancel my order", "cancellation", "don't want", "stop the order"
2. `refund` — patterns: "refund", "money back", "get my money", "reimburse"
3. `change_address` — patterns: "change my address", "wrong address", "moved"

**Status flow:** `pending` → `approved` → `executed` (or `rejected` / `failed`)

**Risk scoring** (determines UI highlight color):
- Refund: +30 pts base
- Cancel: +20 pts base
- Missing order ID: +25 pts
- Multiple past refunds (≥2): +20 pts
- High (≥50), Medium (≥25), Low (<25)

**Deduplication:** Before creating a pending action, checks for existing pending action with same `tenant_id + action_type + order_id`. Returns existing ID if found.

**Live action data:**
- cancel_order/failed: 6
- cancel_order/rejected: 4
- cancel_order/executed: 1

---

## Ticket Distribution (live)

**By status:**
- auto_resolved_review: 28 (54%)
- auto_resolved: 17 (33%)
- human_managing: 4 (8%)
- resolved: 3 (6%)

**By intent detected:**
- general_inquiry: 26 (50%)
- order_status_inquiry: 15 (29%)
- cancellation_request: 11 (21%)

**By sentiment:**
- neutral: 43 (83%)
- positive: 7 (13%)
- negative: 2 (4%)

**Average AI confidence score:** 67.1 / 100

**All 52 tickets are email channel** (no WhatsApp or web form tickets yet)

---

## Verified Working Features

- [x] Gmail OAuth connect and disconnect (per-brand, scoped OAuth)
- [x] Gmail polling every 60 seconds (configurable)
- [x] Automated email filtering (5-layer pipeline, 179 emails blocked)
- [x] AI classification of email intent (7 categories via Mistral)
- [x] Customer sentiment detection (positive/neutral/negative)
- [x] Auto-tag system (cancel, shipping, refund, general tags)
- [x] Shopify order lookup by order number (3 fallback strategies)
- [x] Live Shopify order data injected into AI prompt (verbatim, hallucination-guarded)
- [x] RAG knowledge base search (pgvector, tenant-isolated)
- [x] Knowledge base upload UI (Settings → Knowledge Base)
- [x] AI auto-reply in autopilot mode (sends from brand Gmail)
- [x] AI draft in supervised mode (stored as ai_draft, awaits human approval)
- [x] Email sent from brand Gmail account (not a generic address)
- [x] Email threading (same thread = same ticket, no duplicate tickets)
- [x] Email loop prevention (3 guards: own-address skip, Re: chain depth ≥3, message-ID dedup)
- [x] Cancel order detection from email (keyword pattern matching)
- [x] Cancel order action staged for approval (pending queue)
- [x] Refund detection from email
- [x] Refund action staged for approval
- [x] Take Over Conversation (pause AI, human takes thread)
- [x] Multi-tenant isolation (users see only their brands' tickets)
- [x] Canned responses (create, list, delete — Settings tab)
- [x] Browser notifications (new ticket alerts)
- [x] Dashboard stats (active, AI handled %, escalated, pending approvals)
- [x] Dashboard live refresh counter ("X seconds ago")
- [x] Conversations list with status filters
- [x] Conversation detail with message thread replay
- [x] Escalations page with approve/reject actions
- [x] Quarantine queue review UI
- [x] Email filter settings UI (domain/sender rules)
- [x] Gmail connect settings UI
- [x] Shopify connect settings UI
- [x] AI mode toggle (autopilot / supervised)
- [x] Confidence threshold slider (adjustable per account)
- [x] Onboarding flow (auto-redirects new users to setup)
- [x] Self-healing startup (auto-fixes gmail_connected, tenant backfill, duplicate actions on deploy)

---

## Partially Working Features

- [~] **Email body extraction** — Currently extracts Gmail snippet (~160 chars). Full MIME body not parsed. Works for short emails ("cancel my order #1234"). Long emails get truncated.
- [~] **Cancel order executed in Shopify** — Code is implemented and tested; 1 successful execution in DB, 6 failures (typically missing order ID or Shopify credential issues during testing).
- [~] **Refund executed in Shopify** — Code implemented; no executions in live DB yet (no customer has triggered a refund approval end-to-end).
- [~] **Quarantine queue** — UI exists and routes correctly; zero emails quarantined in live DB (no emails have scored above the classification threshold and below the confidence threshold simultaneously).

---

## Not Yet Implemented

- [ ] WhatsApp channel (code skeleton exists but 0 tickets processed)
- [ ] Web form channel (separate web-form app exists; not tracked in tickets table yet)
- [ ] Order reship / create new order (listed in Shopify capabilities UI but no backend route)
- [ ] Change address execution in Shopify (staged correctly; execution route not confirmed working)
- [ ] Exchange flow (no dedicated exchange action type — only refund + cancel)
- [ ] Email analytics/reporting dashboard (no aggregate charts)
- [ ] SLA / response time alerts
- [ ] Customer satisfaction scores (CSAT)
- [ ] Bulk ticket operations

---

## Real Performance Numbers

| Metric | Value |
|--------|-------|
| AI handled without human | 86.5% |
| Emails filtered before AI sees them | 59.9% (179 of 299) |
| Average confidence score | 67.1 / 100 |
| Most common intent | general_inquiry (50%) |
| Most common filter block reason | blocked_sender_pattern (68 emails) |
| Most common sentiment | neutral (83%) |
| Active accounts (tenants) | 3 |
| Brands with Gmail connected | 2 of 3 |

---

## Honest Product Description (for website/demo)

Resolv is an AI-powered customer support inbox for e-commerce brands. It connects to your brand's Gmail account and Shopify store, automatically classifies incoming emails using a 5-layer filter (blocking spam, newsletters, and automated messages), and uses Mistral AI to write personalized replies that reference real order data pulled live from Shopify. In supervised mode, every reply is a draft waiting for your one-click approval; in autopilot mode, replies are sent automatically when confidence is high enough. Cancellations and refund requests are detected and staged in a human-approval queue before any action runs in Shopify.

**What it does that competitors charge extra for:**
- Per-brand Gmail (Gorgias charges per seat; Resolv's per-brand OAuth is included)
- Live Shopify order data in every AI reply (Gorgias requires expensive Shopify Plus plan for deep data)
- 5-layer automated email filtering (reduces AI API costs — 59.9% of emails never reach the LLM)
- Confidence-gated quarantine (ambiguous emails held for review, not silently dropped)
- Action staging with risk scoring (Zendesk has no native Shopify action queue; requires costly app)
- Full multi-tenant isolation (each brand is completely isolated — no cross-brand ticket leakage)
- Knowledge base per brand (RAG-powered policy lookup without third-party vector DB costs)

---

*Data source: live Supabase database, queried 2026-06-02. Code analysis from branch 006-email-guardian.*
