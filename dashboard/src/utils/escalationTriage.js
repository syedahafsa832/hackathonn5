// Deterministic escalation triage — turns fields the backend already
// computes and stores on a ticket (never a new LLM call, never free-text
// sentiment/urgency invented here) into a priority level and a short,
// human-readable reason. See message_processor.py's _auto_tag_ticket /
// _detect_email_sentiment (keyword-based, no LLM) for where
// customer_sentiment/tags come from, and customer_success_agent.py's
// RESPONSE JSON schema for the fixed `intent` enum.

const PROVIDER_OUTAGE_PREFIX = 'AI reply limit reached';
const HUMAN_REQUEST_REASON = 'Customer explicitly requested a human agent.';

// Only intents that represent a policy-bound action request (refund/return/
// exchange/cancellation/address change) genuinely need a human judgment
// call on THIS ticket type - a plain question (order status, sizing,
// product info) escalated only because confidence was low isn't the same
// kind of "needs a decision" case.
const ACTIONABLE_INTENTS = {
  refund_request: 'Refund',
  return_request: 'Return',
  exchange_request: 'Exchange',
  cancellation_request: 'Cancellation',
  address_change: 'Address change',
};

// _auto_tag_ticket's keyword-derived tags - same actionable categories,
// covering tickets where `intent` wasn't set to one of the enum values
// above (e.g. an older ticket, or a channel that doesn't run intent
// classification) but the message content was still clearly about one of
// these requests.
const ACTIONABLE_TAGS = {
  refund: 'Refund',
  cancel: 'Cancellation',
  exchange: 'Exchange',
  damaged: 'Damaged item',
};

function actionableLabel(ticket) {
  if (ticket.intent && ACTIONABLE_INTENTS[ticket.intent]) return ACTIONABLE_INTENTS[ticket.intent];
  const tags = ticket.tags || [];
  for (const t of tags) {
    if (ACTIONABLE_TAGS[t]) return ACTIONABLE_TAGS[t];
  }
  return null;
}

// Whether the customer already received something from the AI before this
// escalated - vs. being left with nothing at all (a stronger, more urgent
// signal than an escalation the AI at least acknowledged).
export function hasAiResponded(ticket) {
  return !!(ticket.ai_reply || ticket.ai_response || ticket.response_sent || ticket.email_sent || ticket.human_response);
}

// Three levels only, per deterministic rules - never an LLM-assigned score.
// 'urgent'   -> 🔴 human should act immediately
// 'needs_attention' -> 🟠 human should review/decide, not necessarily urgent
// 'other'    -> ⚪ escalated for visibility, no action currently required
export function classifyEscalationPriority(ticket) {
  const reason = ticket.escalation_reason || '';
  const aiResponded = hasAiResponded(ticket);
  const actionable = actionableLabel(ticket);

  if (reason === HUMAN_REQUEST_REASON) {
    return { level: 'urgent', reasonLabel: 'Customer asked for a human', aiResponded };
  }
  if (reason.startsWith(PROVIDER_OUTAGE_PREFIX)) {
    return { level: 'urgent', reasonLabel: 'AI temporarily unavailable', aiResponded };
  }
  if (!aiResponded) {
    return {
      level: 'urgent',
      reasonLabel: actionable ? `${actionable} · Customer blocked, no reply yet` : 'Customer blocked, no reply yet',
      aiResponded,
    };
  }
  if (ticket.risk_level === 'high') {
    return {
      level: 'urgent',
      reasonLabel: actionable ? `${actionable} · High risk` : 'High risk, needs immediate review',
      aiResponded,
    };
  }
  if (ticket.customer_sentiment === 'angry') {
    return { level: 'urgent', reasonLabel: 'Customer sounds angry', aiResponded };
  }

  if (actionable) {
    return { level: 'needs_attention', reasonLabel: `${actionable} · Policy exception`, aiResponded };
  }
  if (ticket.risk_level === 'medium') {
    return { level: 'needs_attention', reasonLabel: 'Needs a judgment call', aiResponded };
  }
  if (reason) {
    return { level: 'needs_attention', reasonLabel: reason, aiResponded };
  }

  return { level: 'other', reasonLabel: 'Escalated for review', aiResponded };
}

// Turns the raw fields already stored on the ticket into a plain-English
// brief for a human agent, without changing how escalation is decided or
// stored. Shared by TicketDetail.jsx (the full brief panel) and Actions.jsx
// (the triage queue's short summary line) so both surfaces stay consistent.
export function buildEscalationBrief(ticket) {
  const customerLine = (ticket.message || '').trim().slice(0, 220);
  const sentiment = ticket.customer_sentiment;

  const whyStopped = ticket.escalation_reason
    || (ticket.risk_level === 'high' && 'The request carries financial/policy risk (e.g. refund, cancellation, or a legal/pricing concern) that needs a human decision.')
    || (sentiment === 'angry' && 'The customer sounds angry or frustrated. Routed to a human to avoid a scripted reply landing badly.')
    || 'The AI was not confident enough in its answer to reply automatically.';

  const orderContext = ticket.detected_order_id
    ? `Order #${ticket.detected_order_id}`
    : 'No order number detected in this conversation.';

  const tags = ticket.tags || [];
  const isProviderOutage = (ticket.escalation_reason || '').startsWith(PROVIDER_OUTAGE_PREFIX);
  let recommendedAction = 'Read the conversation below and reply manually.';
  if (isProviderOutage) {
    recommendedAction = 'Review conversation and reply manually.';
  } else if (tags.includes('cancel') && ticket.detected_order_id) {
    recommendedAction = `Check order #${ticket.detected_order_id} in Shopify. Cancel/restock if it hasn't shipped, otherwise explain why it can't be cancelled.`;
  } else if (tags.includes('refund') && ticket.detected_order_id) {
    recommendedAction = `Verify order #${ticket.detected_order_id} qualifies for a refund, then use the Refund action or explain the policy if it doesn't.`;
  } else if (tags.includes('damaged') || tags.includes('exchange')) {
    recommendedAction = 'Confirm the issue with the customer and arrange a replacement/exchange or refund as appropriate.';
  } else if (ticket.ai_draft) {
    recommendedAction = "Review the AI's suggested draft below. Edit and send it, or write your own reply.";
  }

  let confidencePct = null;
  if (typeof ticket.confidence_score === 'number') {
    confidencePct = ticket.confidence_score <= 1 ? ticket.confidence_score * 100 : ticket.confidence_score;
  }

  return { customerLine, whyStopped, orderContext, recommendedAction, confidencePct };
}
