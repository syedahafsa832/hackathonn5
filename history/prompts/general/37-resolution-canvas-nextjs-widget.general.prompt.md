---
id: 37
title: Resolution Canvas Next.js Chat Widget
stage: general
date: 2026-06-08
surface: agent
model: claude-sonnet-4-6
feature: resolution-canvas
branch: updated_whole_damn_product
user: Syeda Hafsa
command: Build Resolution Canvas widget (Next.js 14, Framer Motion, Tailwind)
labels: ["nextjs","chat-widget","framer-motion","tailwind","ai-ux","shopify"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - resolution-canvas/package.json
  - resolution-canvas/tsconfig.json
  - resolution-canvas/next.config.ts
  - resolution-canvas/tailwind.config.ts
  - resolution-canvas/postcss.config.js
  - resolution-canvas/app/globals.css
  - resolution-canvas/app/layout.tsx
  - resolution-canvas/app/page.tsx
  - resolution-canvas/app/chat-demo/page.tsx
  - resolution-canvas/components/chat-widget/types.ts
  - resolution-canvas/components/chat-widget/ChatWidget.tsx
  - resolution-canvas/components/chat-widget/ResolutionCanvas.tsx
  - resolution-canvas/components/chat-widget/LauncherButton.tsx
  - resolution-canvas/components/chat-widget/AiOrb.tsx
  - resolution-canvas/components/chat-widget/ActionCards.tsx
  - resolution-canvas/components/chat-widget/MessageThread.tsx
  - resolution-canvas/components/chat-widget/ResolutionTracker.tsx
  - resolution-canvas/components/chat-widget/OrderContextCard.tsx
  - resolution-canvas/components/chat-widget/ConfidenceBadge.tsx
  - resolution-canvas/README.md
tests:
  - none (visual/interactive widget — manual browser verification required)
---

## Prompt

# tResolv — Resolution Canvas Chat Widget (Next.js)

[Full spec as provided by user — see conversation for complete details]

Core philosophy: "Resolution over conversation." Build a next-generation AI customer support
chat widget that competes against Zendesk and Gorgias.

Stack: Next.js 14 App Router, Tailwind CSS, Framer Motion, Lucide React, Geist font.

Components required:
- ChatWidget.tsx (orchestrator)
- ResolutionCanvas.tsx (main active view)
- LauncherButton.tsx (animated orb launcher)
- AiOrb.tsx (state-aware animated orb: idle/thinking/acting/resolved/escalated/error)
- ActionCards.tsx (initial 2x2 quick-action tiles)
- MessageThread.tsx (conversation with typing indicator)
- ResolutionTracker.tsx (vertical stepper)
- OrderContextCard.tsx (live Shopify order card)
- ConfidenceBadge.tsx (AI confidence indicator)
- types.ts (shared TypeScript types)

Plus: demo page at /chat-demo, README.md with embed instructions.
Demo mode (apiBaseUrl="demo") uses 1.2s mock responses.

## Response snapshot

Created 20 files in resolution-canvas/ as a standalone Next.js 14 project:

- Full TypeScript strict mode throughout
- Framer Motion variants for AiOrb (6 states), panel open/close, message entrance, typing dots
- LauncherButton: orbiting-arc SVG icon with 8s CSS rotation, pulse keyframe, AnimatePresence morph to X
- ResolutionTracker: Framer Motion staggerChildren (0.15s), active step pulsing ring
- OrderContextCard: live-dot animation via CSS keyframe, status color left-border
- ConfidenceBadge: color-coded dot (green ≥80, amber ≥50, red <50)
- ChatWidget: AnimatePresence transition between ActionCards ↔ ResolutionCanvas
- 3D panel CSS: perspective(1200px) rotateX(0.5deg) on hover, multi-layer box-shadow
- Demo mode: 6 mock response scenarios with order data, confidence, resolution steps
- Mobile: rc-right-col hides on ≤480px, thin progress bar shows instead
- Demo page: fake Shopify storefront (LUNA APPAREL) with product grid + arrow tooltip

## Outcome

- ✅ Impact: Full Resolution Canvas widget scaffolded from scratch. Run `npm install && npm run dev` from resolution-canvas/ to see it at localhost:3001/chat-demo.
- 🧪 Tests: Manual only — click orb, send messages, observe orb state transitions, stepper progression, order card appearance.
- 📁 Files: 20 new files created in resolution-canvas/
- 🔁 Next prompts: npm install → npm run dev → browser test → wire real API URL → deploy
- 🧠 Reflection: Used dynamic import with ssr:false for ChatWidget to avoid hydration mismatch from sessionId generation. All state lives in ChatWidget; child components are pure display with callbacks.

## Evaluation notes (flywheel)

- Failure modes observed: none yet (not run)
- Graders run and results (PASS/FAIL): n/a
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): swap apiBaseUrl="demo" to real backend URL and verify confidence/order_data fields parse correctly
