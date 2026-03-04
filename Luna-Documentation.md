# Luna - Customer Success AI Agent Documentation

## Overview

**Luna** is the V3 Customer Success AI Agent for **Aurelio & Finch** (a luxury streetwear brand). She is defined as "the Senior Brand Stylist" - not a support bot, but a human-like expert who helps clients build their wardrobes.

---

## Capabilities

### 1. RAG-Based Knowledge Retrieval
Luna searches the knowledge base using pgvector similarity to find relevant policies, brand information, and product details.

### 2. Sizing Engine Integration
When customers ask about sizing, Luna uses the **size_engine** to provide recommendations based on:
- Customer height (cm)
- Customer weight (kg)
- Fit preference (true/false)

### 3. Real-Time Tool Calls
Luna can access live data from:
- **Shopify**: Order status, tracking numbers, inventory
- **AfterShip**: Real-time shipping tracking

### 4. Sentiment Analysis
Luna analyzes customer sentiment to detect frustration or dissatisfaction.

---

## Tone and Voice Guidelines

- **Confident & Expert**: Uses phrases like "I recommend," "In my experience," "We've designed this for..."
- **Concise & Fluid**: No bullet points or numbered lists - natural, flowing paragraphs
- **No Jargon**: Never mentions technical terms like "deterministic," "variant," "sizing engine," etc.
- **Style-First**: Focuses on look and feel, not measurements
- **"We" Mentality**: Speaks as part of the brand
- **Soft Close**: Ends with helpful questions that move toward a sale

---

## Confidence Score System

### Starting Confidence
- **Starting score**: 80%

### Confidence Adjustments
| Condition | Adjustment |
|-----------|------------|
| No RAG context found | -15% |
| Negative sentiment detected | -10% |
| Standard low-risk intents (order_status, shipping, sizing, product inquiry) with low risk | +10% |

### Minimum Confidence
- **Minimum enforced**: 30% (if response is valid)

---

## Confidence Thresholds

| Confidence Score | Status | Action |
|------------------|--------|--------|
| Below 30% | `auto_resolved` | Sends response anyway (fallback) |
| Below 70% OR `risk_level` = "high" | `escalated` | Routes to human agent |
| 70% or higher AND `risk_level` != "high" | `auto_resolved` | AI handles automatically |

---

## Escalation Triggers

### Escalation Categories with Confidence Scores

| Category | Trigger Keywords | Confidence Score |
|----------|------------------|------------------|
| **Pricing** | price, cost, pricing, payment, billing, discount, deal, subscription | 90% |
| **Legal** | legal, lawyer, lawsuit, contract, terms, liability, court | 95% |
| **Refund** | refund, return, money back, chargeback, compensation | 85% |
| **Negative Sentiment** | sentiment score below -0.3 | varies |
| **Profanity** | fuck, shit, damn, bitch, asshole, etc. | 95% |
| **Angry Customer** | angry, frustrated, disappointed, mad, furious, livid | 80% |

### Complexity Analysis Triggers

Luna also analyzes message complexity. If any of these are detected, escalation is recommended:
- Technical terms (API, integration, webhook, etc.)
- Multiple question marks (more than 2)
- Multiple exclamation marks (more than 3)
- Long messages (more than 100 words)

If complexity score exceeds 0.5, escalation is recommended.

---

## Response Time Targets by Escalation Category

| Category | Team | Response Time |
|----------|------|---------------|
| Pricing/Sales | Sales | 1 hour |
| Legal | Legal | 30 minutes |
| Billing/Refunds | Billing | 1 hour |
| Senior Support | Senior Agents | 2 hours |
| Security | Security Team | 15 minutes |

---

## Message Processing Flow

1. Extract channel, content, customer email, name
2. Check operational mode (active/paused/manual)
3. Resolve or create customer
4. Get AI analysis from Luna
5. Check confidence threshold (configurable, default 75%)
6. Check for human takeover overrides
7. Decide:
   - **Active mode + high confidence**: Auto-reply sent
   - **Active mode + low confidence OR escalation requested**: Route to human
   - **Paused mode**: Store draft only, don't send
   - **Manual mode**: Create human-only ticket

---

## Configuration

System settings are stored in Supabase and control:
- `ai_mode`: "active" | "paused" | "manual"
- `confidence_threshold`: Default 75%

---

## Key Files

| File | Purpose |
|------|---------|
| `backend/src/agent/customer_success_agent.py` | Main Luna agent implementation |
| `backend/src/services/escalation_service.py` | Escalation trigger detection and analysis |
| `backend/src/services/tools.py` | V3 tools (Shopify, AfterShip integrations) |
| `backend/src/workers/message_processor.py` | Message processing and routing logic |
| `incubation/context/escalation-rules.md` | Detailed escalation rules documentation |
| `specs/001-customer-success-agent/spec.md` | Feature specification |

---

## Summary

Luna is designed to handle most customer inquiries autonomously with a high degree of confidence. She escalates to human agents when:
1. Confidence drops below 70%
2. The request involves high-risk topics (pricing, legal, refunds, profanity, angry customers)
3. The message is complex (technical terms, multiple questions, very long)
4. Sentiment analysis detects strong negative emotion

The escalation system ensures customers always receive appropriate support while Luna handles the majority of routine inquiries.
