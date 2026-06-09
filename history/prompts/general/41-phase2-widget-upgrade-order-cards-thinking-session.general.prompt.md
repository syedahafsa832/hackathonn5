---
id: 41
title: Phase 2 Widget Upgrade — OrderCards, ThinkingIndicator, SatisfactionRating, Session Restore
stage: general
date: 2026-06-09
surface: agent
model: claude-sonnet-4-6
feature: chat-widget
branch: updated_whole_damn_product
user: Syeda Hafsa
command: Phase 2 Widget Upgrade (6 upgrades)
labels: ["chat-widget","resolution-canvas","phase2","order-card","session-restore","satisfaction-rating"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - resolution-canvas/components/chat-widget/types.ts
  - resolution-canvas/components/chat-widget/OrderCard.tsx
  - resolution-canvas/components/chat-widget/ActionResultCard.tsx
  - resolution-canvas/components/chat-widget/ThinkingIndicator.tsx
  - resolution-canvas/components/chat-widget/SatisfactionRating.tsx
  - resolution-canvas/components/chat-widget/MessageThread.tsx
  - resolution-canvas/components/chat-widget/ChatWidget.tsx
  - resolution-canvas/components/chat-widget/ResolutionCanvas.tsx
  - backend/src/api/routes/v2_chat_widget.py
  - backend/src/static/widget.js
  - ai-ops-console/src/pages/Settings.jsx
tests:
  - manual: widget-test.html opened in browser with demo mode
---

## Prompt

Phase 2 Widget Upgrade — implement 6 features:

UPGRADE 1: Inline OrderCard and ActionResultCard components in chat messages (order data renders above text bubble; action result card shows refund/cancel/address/reship state with colored badges).

UPGRADE 2: SmartThinkingIndicator — cycling step labels ("Reading your message…", "Checking order details…", "Pulling Shopify data…", "Writing reply…") when order number detected in user message. Animates between steps every 1.4 s with fade transition.

UPGRADE 3: Brand customization via window.tResolvConfig (brandId, color, botName, brandLabel, apiBase) with data-* attribute fallback in widget.js.

UPGRADE 4: Dashboard Widget Settings page embed code updated to show window.tResolvConfig format instead of data-* attributes.

UPGRADE 5: Post-resolution SatisfactionRating — thumbs up/down shown after resolution_complete message. POSTs to /api/v2/widget/feedback. Shows "Thanks for your feedback!" for 2.2 s then fades.

UPGRADE 6: sessionStorage conversation restore — 4-hour TTL, key tresolv_session_{brandId}, stores last 20 non-typing messages. Shows "— Earlier today —" divider on restore.

## Response snapshot

Implemented all 6 upgrades across frontend and backend:

- Created OrderCard.tsx: status color map (fulfilled/processing/cancelled/refunded/restocked), items list, tracking link, left border accent
- Created ActionResultCard.tsx: CONFIGS map for 4 action types with icon, title, badge, colors; refund_staged/cancel_staged/address_updated/restore_staged
- Created ThinkingIndicator.tsx: replaces TypingIndicator, detects order number with /\b\d{3,6}\b/, 2 or 4 cycling steps, AnimatePresence fade
- Created SatisfactionRating.tsx: thumbs up/down, POST /api/v2/widget/feedback, 2.2 s thank-you fade
- Updated MessageThread.tsx: added sessionId/apiBaseUrl/hasSavedSession props, OrderCard + ActionResultCard above bubbles, SatisfactionRating after resolutionComplete, "Earlier today" divider
- Updated ChatWidget.tsx: loadSession/saveSession with 4h TTL sessionStorage, replyMsg includes orderData/actionResult/resolutionComplete
- Updated ResolutionCanvas.tsx: added sessionId/apiBaseUrl/hasSavedSession to props interface, forwarded to MessageThread
- Updated types.ts: ActionResult discriminated union, updated Message/OrderData/ApiResponse
- Backend v2_chat_widget.py: added action_result + resolution_complete to ChatResponse; added _map_action_result helper; added POST /widget/feedback endpoint writing to chat_feedback table
- widget.js: window.tResolvConfig support with data-* fallback
- Settings.jsx: embed code now generates window.tResolvConfig block; customization docs updated to camelCase keys

## Outcome

- ✅ Impact: Full widget Phase 2 feature set implemented; chat messages now show structured order/action cards; thinking animation contextual to query; session persists across page reloads for 4 hours; satisfaction rating collected after resolutions
- 🧪 Tests: Manual testing via widget-test.html in demo mode
- 📁 Files: 11 files created or modified
- 🔁 Next prompts: Test with live backend; add chat_feedback table migration to Supabase; consider debounced auto-email capture
- 🧠 Reflection: ResolutionCanvas props interface was the key blocker — TypeScript compile would have failed without forwarding sessionId/apiBaseUrl/hasSavedSession through the component tree

## Evaluation notes (flywheel)

- Failure modes observed: Props threading across ChatWidget → ResolutionCanvas → MessageThread required careful type updates at each layer
- Graders run and results (PASS/FAIL): TypeScript types manually verified
- Prompt variant (if applicable): null
- Next experiment: Add chat_feedback Supabase table and test feedback persistence
