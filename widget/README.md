# Resolution Canvas — AI Support Widget

A next-generation AI customer support widget built for Shopify brands. Competes directly against Zendesk and Gorgias.

**Philosophy: Resolution over Conversation.**

---

## Quick Start

```bash
cd resolution-canvas
npm install
npm run dev
# → http://localhost:3001/chat-demo
```

The demo page shows the widget embedded on a fake Shopify storefront. Click the orbiting-dot launcher in the bottom-right corner to open it.

---

## Project Structure

```
resolution-canvas/
  app/
    layout.tsx            Root layout (Geist font, CSS variables)
    globals.css           Design tokens, panel class, keyframes
    chat-demo/page.tsx    Demo Shopify storefront
  components/chat-widget/
    ChatWidget.tsx        Main orchestrator — manages all state
    ResolutionCanvas.tsx  Two-column layout when conversation is active
    LauncherButton.tsx    Animated 56px orb launcher
    AiOrb.tsx             State-aware animated orb (idle/thinking/acting/resolved…)
    ActionCards.tsx       Initial 2×2 quick-action tiles
    MessageThread.tsx     Scrollable conversation with typing indicator
    ResolutionTracker.tsx Vertical stepper showing resolution progress
    OrderContextCard.tsx  Live Shopify order data card
    ConfidenceBadge.tsx   AI confidence indicator under agent messages
    types.ts              Shared TypeScript types
```

---

## Embedding on any website

### Option A — React / Next.js

```tsx
import { ChatWidget } from '@/components/chat-widget/ChatWidget'

<ChatWidget
  brandId="your-brand-uuid"     // from tResolv Settings → Chat Widget
  orgId="your-org-uuid"
  agentName="Luna"               // optional, default: "Luna"
  accentColor="#6366F1"          // optional, default: indigo
  apiBaseUrl="https://your-api.railway.app"
/>
```

### Option B — Plain HTML / Shopify (script tag)

Use the existing vanilla widget served by the backend:

```html
<!-- Paste before </body> in your Shopify theme.liquid -->
<script
  src="https://YOUR-API-URL/widget.js"
  data-brand="YOUR-BRAND-UUID"
  data-color="#6366F1"
  data-bot-name="Luna"
  data-brand-label="AI Support">
</script>
```

The `resolution-canvas` project is the React/Next.js version with the full Resolution Canvas UX (stepper, order card, confidence badge). The vanilla `widget.js` is the lightweight embed for any HTML site.

---

## Props

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `brandId` | `string` | required | Your tResolv brand UUID |
| `orgId` | `string` | required | Your tResolv org UUID |
| `agentName` | `string` | `"Luna"` | AI agent display name |
| `accentColor` | `string` | `"#6366F1"` | Hex color for buttons and user bubbles |
| `apiBaseUrl` | `string` | required | Backend URL. Pass `"demo"` for mock mode |

---

## API Contract

**Request** — `POST {apiBaseUrl}/api/v2/widget/chat`

```json
{
  "message": "Track my order",
  "session_id": "cs_abc123",
  "brand_id": "uuid",
  "org_id": "uuid",
  "source": "chat",
  "conversation_history": [
    { "role": "user", "content": "..." },
    { "role": "assistant", "content": "..." }
  ]
}
```

**Response**

```json
{
  "reply": "I found your order #1007…",
  "confidence": 92,
  "resolution_step": "gathering",
  "order_data": {
    "orderNumber": "1007",
    "items": [{ "name": "Essential Hoodie V10", "quantity": 1, "price": "Rs 120" }],
    "status": "cancelled",
    "paymentStatus": "paid",
    "cancelledAt": "Jun 5"
  },
  "customer_name": "Aisha"
}
```

`resolution_step` drives the stepper: `understanding → gathering → acting → verifying → resolved`

---

## Design System

| Token | Value | Usage |
|-------|-------|-------|
| `--bg-base` | `#080B14` | Panel background |
| `--accent-primary` | `#6366F1` | Buttons, user bubbles |
| `--state-resolved` | `#10B981` | Resolved state, success |
| `--state-thinking` | `#3B82F6` | AI thinking state |
| `--text-muted` | `rgba(255,255,255,0.25)` | Timestamps, labels |

---

## What makes this different

| Feature | tResolv | Zendesk | Gorgias |
|---------|---------|---------|---------|
| Resolution stepper | ✓ visible to customer | ✗ | ✗ |
| Live order card | ✓ structured UI | text only | text only |
| Confidence badge | ✓ radical transparency | ✗ | ✗ |
| Action cards | ✓ zero-friction start | blank input | blank input |
| Orb state | ✓ ambient AI awareness | ✗ | ✗ |
| 3D panel | ✓ premium physical feel | flat | flat |
