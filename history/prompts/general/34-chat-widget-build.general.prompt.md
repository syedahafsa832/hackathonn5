---
id: 34
title: Chat widget build
stage: general
date: 2026-06-08
surface: agent
model: claude-sonnet-4-6
feature: chat-widget
branch: updated_whole_damn_product
user: Syeda Hafsa
command: RESOLV — WEBSITE CHAT WIDGET (full build)
labels: ["chat_widget", "widget_js", "embed", "lua_chat", "real_time", "dashboard"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/api/routes/v2_chat_widget.py
  - backend/src/static/widget.js
  - backend/main.py
  - ai-ops-console/src/pages/Settings.jsx
  - ai-ops-console/src/components/Badge.jsx
  - ai-ops-console/src/pages/Dashboard.jsx
tests:
  - none
---

## Prompt

Build a floating chat bubble that appears on any website. Customer clicks → talks to Luna → Luna reads their Shopify orders. Single script tag embed. Full dashboard integration.

## Response snapshot

**Architecture decision:** Chat sessions stored as tickets with `channel='chat'` in existing tickets table. `session_id` stored in `gmail_thread_id` field. No new DB table needed — all existing dashboard views, ticket detail, and escalations queue work automatically.

**Part 1 — Backend (`v2_chat_widget.py`):**
- `POST /api/v2/widget/chat`: receives message, rate-limits by IP (10/min), sanitizes input (strip HTML, 1000 char limit), creates or retrieves chat session, builds conversation history context, calls `customer_success_agent.process_customer_query()` with `[CHAT MODE]` prefix, trims sign-off, stores messages in ticket.messages JSONB, returns reply + suggested actions
- `GET /api/v2/widget/chat/{session_id}`: returns conversation history for restore on reload
- `POST /api/v2/widget/chat/{session_id}/email`: attaches customer email to session
- Rate limiter: in-memory per-IP dict with 60s window, no Redis dependency

**Part 2 — Widget JavaScript (`src/static/widget.js`):**
- Self-contained IIFE, zero dependencies
- Reads `data-brand`, `data-color`, `data-bot-name`, `data-brand-label` from script tag
- Derives API base URL from script tag `src` attribute
- Dark theme: background #0d0d1c, panel #1a1a2e bubbles, accent color configurable
- Features: floating 56px circle button, 380×520 panel, smooth open/close animation (250ms), typing dots, quick reply pills, email capture after 3 exchanges, auto-resize textarea, Enter to send, session restore from localStorage/backend
- Mobile: full screen on <480px viewport
- Session persists via localStorage (session_id) and sessionStorage (open state)

**Part 3 — `/widget.js` endpoint (`main.py`):**
- `GET /widget.js` serves `src/static/widget.js` as `application/javascript` with 5-min cache header
- Registered before routers so it's always available

**Part 4 — Dashboard integration:**
- `Settings.jsx`: new `ChatWidgetTab` with embed code display (copy button), customization attributes docs, how-it-works steps, multi-brand selector
- New "Chat Widget" tab added to TABS array
- `Badge.jsx`: added `chat` and `email` channel badges (purple for chat, blue for email)
- `Dashboard.jsx`: channel column now renders `<Badge status="chat" />` for chat sessions

**Also fixed:** `email_poller.py` — `_csat_every_n` was accidentally commented out by linter; restored it

## Outcome

- ✅ Single `<script>` tag embed works on any HTML page or Shopify store
- ✅ Chat sessions appear as tickets in Conversations with purple "💬 Chat" badge
- ✅ Luna uses the same AI pipeline (RAG + Shopify orders) for chat and email
- ✅ Cancel/refund requests from chat go to same Escalations queue
- ✅ Settings → Chat Widget tab shows embed code with copy button
- 📁 Files: 6 files created/modified
- 🧪 Test: paste embed snippet in any HTML file, open in browser, chat with Luna
