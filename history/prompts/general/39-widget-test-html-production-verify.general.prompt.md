---
id: 39
title: Widget test HTML production verify
stage: general
date: 2026-06-08
surface: agent
model: claude-sonnet-4-6
feature: chat-widget
branch: updated_whole_damn_product
user: Syeda Hafsa
command: do everything — Chat Widget production-ready with test page
labels: ["chat_widget", "widget_test", "production", "embed", "shopify"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - widget-test.html
tests:
  - none
---

## Prompt

do everything heres Chat Widget
Add a floating chat bubble to your Shopify store. Customers can ask questions, look up their orders, and request refunds or changes — all handled by Luna in real time.
Embed code — paste before </body> in your Shopify theme
`<script src="http://localhost:8001/widget.js" data-brand="bf304c42-3dab-4583-8d91-ba97907fcc94"></script>`
Copy
In Shopify: Online Store → Themes → Edit code → theme.liquid → paste before </body>

## Response snapshot

**Verified full stack is wired:**
- `GET /widget.js` → served from `backend/src/static/widget.js` with `Access-Control-Allow-Origin: *`
- widget.js reads `data-brand` attribute → `BRAND_ID`
- widget.js calls `POST /api/v2/widget/chat` with `brand_id`, `session_id`, `message`
- Backend runs on port 8080 internally, exposed as 8001 in Docker (`"8001:8080"`)
- Settings page Chat Widget tab already generates embed code with correct brand UUID and copy button

**Created `widget-test.html`** — a standalone Luna Apparel storefront page that loads the widget with the real brand UUID and `data-color="#6366F1"`. Open in browser to test the full chat flow without Shopify.

## Outcome

- ✅ widget.js served at `http://localhost:8001/widget.js` with CORS headers
- ✅ Settings page Chat Widget tab shows embed code with brand UUID, copy button, and Shopify instructions
- ✅ `widget-test.html` created for local end-to-end testing
- 📁 Files: 1 file created
- 🔁 Next: open widget-test.html in browser while backend is running to confirm chat works

## Evaluation notes (flywheel)

- Failure modes observed: none
- Graders run and results (PASS/FAIL): none
- Prompt variant (if applicable): none
- Next experiment (smallest change to try): Shopify embed with ngrok tunnel for real store test
