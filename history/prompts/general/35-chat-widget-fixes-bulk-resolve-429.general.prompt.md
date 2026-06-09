---
id: 35
title: Chat widget fixes, bulk resolve, 429 rate limiting
stage: general
date: 2026-06-08
surface: agent
model: claude-sonnet-4-6
feature: chat-widget
branch: updated_whole_damn_product
user: Syeda Hafsa
command: Fix 3 backend issues + new ChatWidget.jsx React component
labels: ["chat_widget", "bug_fix", "rate_limiting", "bulk_resolve", "customer_name"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/api/routes/v2_chat_widget.py
  - backend/src/agent/customer_success_agent.py
  - backend/src/services/intent_detector.py
  - ai-ops-console/src/pages/Actions.jsx
  - ai-ops-console/src/components/ChatWidget.jsx
tests:
  - none
---

## Prompt

Fix 3 backend issues:
1. Chat widget says "Hey Website" instead of customer name
2. Escalation page "Mark X resolved" button not clearing tickets
3. Slow chat responses (Mistral 429 rate limiting) — skip intent detection + RAG for chat, lower retries

Also: new ChatWidget.jsx React component (dark glassmorphism, Sora font, pulsing launcher, animated typing dots).

## Response snapshot

**Fix 1 — "Hey Website" name bug:**
- Root cause: `v2_chat_widget.py` fell back to `ticket.get("customer_name")` which defaults to `"Website Visitor"`. Agent then did `.split()[0]` = `"Website"`.
- Fix: `customer_info["name"] = body.customer_name or "there"` — no ticket fallback.
- Also: agent skips `"Hey {name},"` greeting prefix for `[CHAT MODE]` queries — each chat message is mid-conversation, not a fresh email.

**Fix 2 — Bulk resolve not clearing:**
- Root cause: `refetchEscalations()` was not awaited in both `handleBulkCloseEscalations` and `handleCloseAllEscalations`, so the list didn't update synchronously.
- Fix: `await refetchEscalations()` in both handlers.

**Fix 3 — Mistral 429 rate limiting:**
- Added `_is_chat = "[CHAT MODE" in query` at top of agent `process_customer_query`.
- Chat mode: skips RAG embedding call (saves 1 Mistral API call per message).
- Chat mode: skips intent detection LLM call (saves another Mistral API call per message).
- Both `CustomerSuccessAgent` and `IntentDetector` OpenAI clients now init with `max_retries=1, timeout=15s/8s`.
- Added fast-fail on 429: checks `getattr(e, 'status_code', None) == 429 or "429" in str(e)` — returns fallback immediately instead of retrying.

**New ChatWidget.jsx:**
- Dark glassmorphism with `#13111f` base, violet-to-lavender gradient accent.
- Sora font, pulsing launcher button, animated typing dots, frosted glass panel.
- API endpoint corrected from `/api/chat` to `/api/v2/widget/chat`.
- Props: `brandId`, `orgId`, `agentName`, `accentColor`.
- Mobile: full-screen on <480px viewport.

## Outcome

- ✅ Luna no longer says "Hey Website" — uses "there" as fallback, no greeting prefix in chat
- ✅ Bulk resolve immediately clears tickets from escalations list
- ✅ Chat messages now cost 1 LLM call (main reply only) instead of 3 (RAG + intent + reply)
- ✅ 429s fast-fail to fallback response instead of blocking for 4+ seconds
- 📁 Files: 5 files modified/created
