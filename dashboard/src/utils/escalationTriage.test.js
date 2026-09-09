// Plain-Node test (no test framework configured in this project) — run with:
//   node src/utils/escalationTriage.test.js
import assert from 'node:assert/strict';
import { classifyEscalationPriority, hasAiResponded, buildEscalationBrief } from './escalationTriage.js';

function ticket(overrides = {}) {
  return {
    id: 't1', customer_email: 'bushrazohaib84@gmail.com', customer_name: 'Bushra Zohaib',
    status: 'escalated', ai_reply: 'Noted, a team member will follow up.',
    ...overrides,
  };
}

let passed = 0;
function test(name, fn) {
  try { fn(); passed++; console.log(`ok - ${name}`); }
  catch (e) { console.error(`FAIL - ${name}\n  ${e.message}`); process.exitCode = 1; }
}

test('1. genuine human-action escalation (explicit human request) is urgent', () => {
  const t = ticket({ escalation_reason: 'Customer explicitly requested a human agent.' });
  assert.equal(classifyEscalationPriority(t).level, 'urgent');
});

test('2. exchange policy-exception escalation (ticket #7767d0be shape) is needs_attention', () => {
  const t = ticket({ intent: 'exchange_request', detected_order_id: '1009', risk_level: 'medium', escalation_reason: null });
  const c = classifyEscalationPriority(t);
  assert.equal(c.level, 'needs_attention');
  assert.match(c.reasonLabel, /Exchange/);
  assert.match(c.reasonLabel, /Policy exception/);
});

test('3. normal/non-actionable escalation (low risk, no reason, general inquiry) stays other', () => {
  const t = ticket({ intent: 'product_inquiry', risk_level: 'low', escalation_reason: null, customer_sentiment: 'neutral' });
  assert.equal(classifyEscalationPriority(t).level, 'other');
});

test('4a. high risk_level is surfaced as urgent', () => {
  const t = ticket({ risk_level: 'high', escalation_reason: null });
  assert.equal(classifyEscalationPriority(t).level, 'urgent');
});

test('4b. angry customer_sentiment is surfaced as urgent', () => {
  const t = ticket({ customer_sentiment: 'angry', risk_level: 'low', escalation_reason: null });
  assert.equal(classifyEscalationPriority(t).level, 'urgent');
});

test('4c. provider-outage escalation_reason is surfaced as urgent', () => {
  const t = ticket({ escalation_reason: 'AI reply limit reached — every connected AI model is temporarily out of quota.' });
  assert.equal(classifyEscalationPriority(t).level, 'urgent');
});

test('4d. no AI response at all (customer blocked) is surfaced as urgent', () => {
  const t = ticket({ ai_reply: null, ai_response: null, response_sent: false, email_sent: false, human_response: null, risk_level: 'low' });
  const c = classifyEscalationPriority(t);
  assert.equal(c.level, 'urgent');
  assert.equal(c.aiResponded, false);
});

test('5. missing order context does not break classification', () => {
  const t = ticket({ intent: 'exchange_request', detected_order_id: undefined, detected_order_number: undefined, order_id: undefined });
  assert.doesNotThrow(() => classifyEscalationPriority(t));
  assert.equal(classifyEscalationPriority(t).level, 'needs_attention');
});

test('9. an already-responded ticket is reported as such', () => {
  const t = ticket({ intent: 'exchange_request', ai_reply: 'Noted, our team will follow up.' });
  assert.equal(classifyEscalationPriority(t).aiResponded, true);
});

test('hasAiResponded: true when any known reply/response field is set, false otherwise', () => {
  assert.equal(hasAiResponded({ ai_reply: 'x' }), true);
  assert.equal(hasAiResponded({ email_sent: true }), true);
  assert.equal(hasAiResponded({}), false);
});

test('buildEscalationBrief: shared with TicketDetail.jsx, does not throw, quotes real message text', () => {
  const brief = buildEscalationBrief({ message: 'wrong size on order #1009, exchange for another size' });
  assert.ok(brief.customerLine.includes('order #1009'));
  assert.equal(typeof brief.whyStopped, 'string');
});

console.log(`\n${passed} test(s) passed`);
