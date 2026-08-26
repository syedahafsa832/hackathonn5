import { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import client from '../api/client';
import Badge from '../components/Badge';
import ActionCard from '../components/ActionCard';
import Alert from '../components/Alert';
import { useSendMessage, useTakeover, useRelease, useTicket, useMarkRead } from '../hooks/useApi';

// Mirrors api.getConversationMessages()'s merge logic, but runs on a ticket
// object we already have in memory instead of firing a second network
// request for the same /api/tickets/:id payload useTicket just fetched.
function normalizeTicketMessages(ticket) {
  if (!ticket) return [];

  let msgs = ticket.messages || [];
  if (typeof msgs === 'string') {
    try { msgs = JSON.parse(msgs); } catch { msgs = []; }
  }

  const thread = msgs.map(m => ({
    ...m,
    role: m.direction === 'inbound' ? 'user' : m.role || 'ai',
    content: m.body || m.content || '',
    isDraft: m.direction === 'draft',
  }));

  const hasInbound = thread.some(m => m.role === 'user');
  if (!hasInbound) {
    const customerBody = ticket.message || ticket.content || ticket.body || ticket.email_body;
    if (customerBody) {
      thread.unshift({ role: 'user', content: customerBody, created_at: ticket.created_at });
    }
  }

  const hasAiMessage = thread.some(m => m.role === 'ai' || m.role === 'assistant');
  if (!hasAiMessage) {
    const aiText = ticket.ai_reply || ticket.ai_draft || ticket.ai_response;
    if (aiText) {
      thread.push({
        role: 'ai',
        content: aiText,
        created_at: ticket.updated_at,
        isDraft: !ticket.ai_reply,
      });
    }
  }

  return thread.filter((m, i) => {
    const prev = thread[i - 1];
    return !prev || prev.role !== m.role || prev.content !== m.content;
  });
}

// The reply-suggestions AI call is prompted to return {short, detailed,
// empathetic} as flat strings, but doesn't always comply — occasionally one
// field comes back as a nested object instead (e.g. { text: "...", tone:
// "brief" }). Pull a usable string out of that shape rather than letting it
// fall through to a bare String(value), which stringifies any object to the
// literal text "[object Object]".
function extractReplyText(value) {
  if (typeof value === 'string') return value;
  if (value == null) return '';
  if (typeof value === 'object') {
    const nested = value.text ?? value.content ?? value.value ?? value.reply ?? value.message;
    if (typeof nested === 'string') return nested;
    return '';
  }
  return String(value);
}

const ACTION_TYPE_MAP = {
  CANCEL: 'cancel_order',
  REFUND: 'refund',
  ADDRESS_CHANGE: 'change_address',
};

function OrderPanel({ ticketId, ticket }) {
  const [order, setOrder] = useState(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(null);
  const [actionResult, setActionResult] = useState(null);
  const [stagingAction, setStagingAction] = useState('');

  useEffect(() => {
    client.get(`/api/v2/tickets/${ticketId}/order`)
      .then(res => setOrder(res.data?.order || null))
      .catch(() => setOrder(null))
      .finally(() => setLoading(false));
  }, [ticketId]);

  // Cancel / Refund — execute immediately in Shopify + send confirmation email
  const executeAction = async (type) => {
    const orderLabel = order?.order_name || (order?.order_number ? `#${order.order_number}` : 'this order');
    const confirmMsg = type === 'cancel'
      ? `Cancel ${orderLabel} for ${ticket?.customer_email}? This cannot be undone.`
      : `Issue a full refund for ${orderLabel}? This cannot be undone.`;
    if (!window.confirm(confirmMsg)) return;

    setActionLoading(type);
    setActionResult(null);
    try {
      const res = await client.post(`/api/v2/tickets/${ticketId}/actions/${type}`, {}, { timeout: 30000 });
      const msg = res.data?.message || 'Done.';
      const emailNote = res.data?.email_sent === false
        ? ' Confirmation email could not be sent. Check Gmail connection.'
        : '';
      setActionResult({ ok: true, msg: msg + emailNote });
      setTimeout(() => window.location.reload(), 1800);
    } catch (err) {
      setActionResult({ ok: false, msg: err.response?.data?.detail || err.message || 'Action failed' });
    } finally {
      setActionLoading(null);
    }
  };

  // Address change / Reship — stage for approval queue
  const stageAction = async (type) => {
    setStagingAction(type);
    const actionType = ACTION_TYPE_MAP[type] || type.toLowerCase();
    try {
      await client.post(`/api/v1/actions/create`, {
        ticket_id: ticketId,
        action_type: actionType,
        order_id: order?.id || order?.order_number,
        customer_email: ticket?.customer_email || '',
        customer_name: ticket?.customer_name || ticket?.customer_email || '',
        ai_reasoning: `Manually staged by brand owner from conversation detail`,
      });
      setStagingAction('done:' + type);
    } catch {
      setStagingAction('err:' + type);
    }
  };

  if (loading) {
    return <div className="skeleton" style={{ height: '120px', borderRadius: '6px' }} />;
  }

  return (
    <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', padding: '16px 20px' }}>
      <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '12px' }}>Order Context</div>
      {!order ? (
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', lineHeight: '1.5' }}>
          No order number detected in this ticket. If the customer mentioned one, it will appear here.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div style={{ fontWeight: '700', fontSize: '14px' }}>{order.order_name}</div>
            <div style={{ display: 'flex', gap: '4px' }}>
              <span style={{ fontSize: '11px', padding: '2px 6px', borderRadius: '3px', background: order.financial_status === 'paid' ? 'var(--success-light)' : 'var(--bg-tertiary)', color: order.financial_status === 'paid' ? 'var(--success)' : 'var(--text-muted)', fontWeight: '600' }}>{order.financial_status}</span>
              <span style={{ fontSize: '11px', padding: '2px 6px', borderRadius: '3px', background: 'var(--bg-tertiary)', color: 'var(--text-muted)', fontWeight: '600' }}>{order.fulfillment_status || 'unfulfilled'}</span>
              {order.cancelled_at && (
                <span style={{ fontSize: '11px', padding: '2px 6px', borderRadius: '3px', background: 'var(--bg-tertiary)', color: 'var(--text-muted)', fontWeight: '600' }}>cancelled</span>
              )}
            </div>
          </div>
          {(order.customer_email || order.created_at) && (
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', gap: '2px' }}>
              {order.customer_email && <div>Customer: {order.customer_email}</div>}
              {order.created_at && <div>Created: {formatDate(order.created_at)}</div>}
            </div>
          )}
          {order.line_items?.length > 0 && (
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
              {order.line_items.map((item, i) => (
                <div key={i}>{item.quantity}× {item.title}{item.variant_title ? ` (${item.variant_title})` : ''}</div>
              ))}
            </div>
          )}
          <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            <div>Total: {order.currency} {order.total_price}</div>
            {order.tracking_number && (
              <div style={{ marginTop: '2px' }}>
                Tracking: {order.carrier ? `${order.carrier} ` : ''}{order.tracking_number}
                {order.tracking_url && <a href={order.tracking_url} target="_blank" rel="noopener noreferrer" style={{ marginLeft: '6px', color: 'var(--accent)', fontSize: '11px' }}>Track →</a>}
              </div>
            )}
          </div>

          <Alert variant={actionResult?.ok ? 'success' : 'error'} style={{ fontSize: '12px' }}>{actionResult?.msg}</Alert>

          {(() => {
            const isRestocked = !!order.cancelled_at && order.fulfillment_status === 'restocked';
            return isRestocked ? (
              <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-muted)', padding: '8px 10px', background: 'rgba(255,255,255,0.03)', borderRadius: '6px', borderTop: '1px solid var(--border)', paddingTop: '10px' }}>
                Order was cancelled and inventory restocked. Cannot be restored. Customer must place a new order.
              </div>
            ) : null;
          })()}

          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '10px', display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            {/* Refund — only when paid and not cancelled+restocked */}
            {order.financial_status === 'paid' && !order.cancelled_at && (
              <button
                onClick={() => executeAction('refund')}
                disabled={!!actionLoading}
                style={{ padding: '5px 10px', fontSize: '11px', borderRadius: '3px', border: '1px solid var(--border)', background: 'var(--bg-secondary)', color: 'var(--text-secondary)', cursor: actionLoading ? 'not-allowed' : 'pointer', fontWeight: '500' }}
              >
                {actionLoading === 'refund' ? 'Refunding...' : 'Refund'}
              </button>
            )}

            {/* Cancel — only when not fulfilled and not already cancelled */}
            {order.fulfillment_status !== 'fulfilled' && !order.cancelled_at && (
              <button
                onClick={() => executeAction('cancel')}
                disabled={!!actionLoading}
                style={{ padding: '5px 10px', fontSize: '11px', borderRadius: '3px', border: '1px solid var(--border)', background: 'var(--bg-secondary)', color: 'var(--text-secondary)', cursor: actionLoading ? 'not-allowed' : 'pointer', fontWeight: '500' }}
              >
                {actionLoading === 'cancel' ? 'Cancelling...' : 'Cancel'}
              </button>
            )}

            {/* Address change + Reship — stage for approval queue; hidden when restocked */}
            {[
              { type: 'ADDRESS_CHANGE', label: 'Update Address', show: order.fulfillment_status !== 'fulfilled' && !order.cancelled_at },
              { type: 'RESHIP', label: 'Reship', show: !(order.cancelled_at && order.fulfillment_status === 'restocked') },
            ].filter(a => a.show).map(({ type, label }) => (
              <button
                key={type}
                onClick={() => stageAction(type)}
                disabled={!!actionLoading || stagingAction === type}
                style={{ padding: '5px 10px', fontSize: '11px', borderRadius: '3px', border: '1px solid var(--border)', background: stagingAction === 'done:' + type ? 'var(--success-light)' : 'var(--bg-secondary)', color: stagingAction === 'done:' + type ? 'var(--success)' : 'var(--text-secondary)', cursor: 'pointer', fontWeight: '500' }}
              >
                {stagingAction === 'done:' + type ? '✓ Queued' : stagingAction === 'err:' + type ? '✗ Failed' : label}
              </button>
            ))}
          </div>
          {stagingAction.startsWith('done:') && (
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>
              Go to <strong>Escalations</strong> to approve and execute.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Real backend-driven processing activity — every row comes from
// ticket.events (an actual dispatch point in the pipeline persisted it),
// never a frontend-invented "thinking" animation. status: 'done' | 'failed'
// (currently always 'done' — a stage that never got a corresponding
// follow-up event, e.g. "order_lookup" with no later "order_found", simply
// stays the last line shown while the ticket is still processing; nothing
// is rendered as complete until the backend actually wrote that row).
function ActivityTimeline({ events, ticketStatus }) {
  if (!events || events.length === 0) return null;
  const stillProcessing = ticketStatus === 'processing';
  return (
    <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', padding: '14px 20px' }}>
      <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '10px' }}>Activity</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {events.map((ev, i) => {
          const isLast = i === events.length - 1;
          const failed = ev.status === 'failed';
          const icon = failed ? '⚠' : '✓';
          const color = failed ? 'var(--warning)' : 'var(--success)';
          return (
            <div key={ev.id || i} style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', fontSize: '12.5px' }}>
              <span style={{ color, flexShrink: 0 }}>{icon}</span>
              <span style={{ color: 'var(--text-secondary)' }}>{ev.label}</span>
              {isLast && stillProcessing && !failed && (
                <span style={{ color: 'var(--text-muted)' }}>…</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function cleanEmailBody(raw) {
  if (raw == null || raw === '') return '';
  // Ticket message bodies are almost always strings, but a non-string value
  // slipping through (e.g. a bare order number) must not crash the whole page —
  // coerce defensively instead of assuming .replace() exists.
  let text = String(raw)
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&nbsp;/g, ' ');
  // Strip Gmail/Outlook quoted-reply chain ("On [date] ... wrote:" and everything after)
  text = text.replace(/\r\n/g, '\n');
  const lines = text.split('\n');
  const clean = [];
  for (const line of lines) {
    if (/^On .{10,} wrote:/.test(line.trim())) break;
    if (/^-{3,}/.test(line.trim()) && clean.length > 0) break;
    if (line.trim().startsWith('>')) continue;
    clean.push(line);
  }
  return clean.join('\n').trim();
}

function ChatBubble({ message, role }) {
  const isCustomer = role === 'customer';
  const isAI = role === 'ai';
  const isDraft = message.isDraft;
  const body = cleanEmailBody(message.content || message.message || message.text || '');
  return (
    <div style={{ display: 'flex', justifyContent: isCustomer ? 'flex-start' : 'flex-end', marginBottom: '12px' }}>
      <div style={{ maxWidth: '70%' }}>
        {!isCustomer && (
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '3px', textAlign: 'right', fontWeight: '500' }}>
            {isAI ? 'AI' : 'You'}{isDraft ? ' · Draft (not sent)' : ''}
          </div>
        )}
        <div style={{
          padding: '10px 14px',
          borderRadius: isCustomer ? '4px 8px 8px 4px' : '8px 4px 4px 8px',
          background: isCustomer ? 'var(--bg-tertiary)' : isAI ? (isDraft ? 'transparent' : 'var(--accent)') : 'var(--bg-primary)',
          color: isCustomer ? 'var(--text-primary)' : isAI ? (isDraft ? 'var(--text-secondary)' : 'white') : 'var(--text-primary)',
          border: isDraft ? '1px dashed var(--border)' : (!isCustomer && !isAI) ? '1px solid var(--border)' : 'none',
          fontSize: '14px',
          lineHeight: '1.5',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          opacity: isDraft ? 0.85 : 1,
        }}>
          {body}
        </div>
        <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '3px', textAlign: isCustomer ? 'left' : 'right' }}>
          {formatDate(message.created_at || message.timestamp)}
        </div>
      </div>
    </div>
  );
}

function ConfidenceBar({ score }) {
  // score may be 0-100 integer (from DB) or 0-1 decimal (from analysis endpoint)
  const pct = Math.round(score > 1 ? score : (score || 0) * 100);
  const color = pct >= 80 ? 'var(--success)' : pct >= 50 ? 'var(--warning)' : 'var(--danger)';
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px', fontSize: '12px' }}>
        <span style={{ color: 'var(--text-secondary)' }}>AI Confidence</span>
        <span style={{ fontFamily: 'DM Mono, monospace', fontWeight: '500', color }}>{pct}%</span>
      </div>
      <div style={{ height: '6px', background: 'var(--bg-tertiary)', borderRadius: '3px', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: '3px', transition: 'width 0.4s ease' }} />
      </div>
    </div>
  );
}

const RISK_COPY = {
  high: { label: 'High risk', color: 'var(--danger, #EF4444)' },
  medium: { label: 'Medium risk', color: 'var(--warning, #F59E0B)' },
  low: { label: 'Low risk', color: 'var(--success, #10B981)' },
};

// Turns the raw fields already stored on the ticket into a plain-English brief for
// a human agent, without changing how escalation is decided or stored.
function buildEscalationBrief(ticket) {
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
  const isProviderOutage = (ticket.escalation_reason || '').startsWith('AI reply limit reached');
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

export default function TicketDetail() {
  const { ticket_id } = useParams();
  const navigate = useNavigate();
  const scrollRef = useRef(null);
  
  const { data: ticket, isLoading: ticketLoading, error: ticketError } = useTicket(ticket_id);
  // Derived from the same ticket fetch above — no separate request, so there's
  // no window where the ticket has loaded but messages haven't (or vice versa).
  const messages = normalizeTicketMessages(ticket);

  const { mutate: sendMessage, isLoading: sending } = useSendMessage();
  const { mutate: takeover } = useTakeover();
  const { mutate: release } = useRelease();
  const [reply, setReply] = useState('');
  const [actionStatus, setActionStatus] = useState('');
  const [sendStatus, setSendStatus] = useState('');
  const [approving, setApproving] = useState(false);
  const [suggestions, setSuggestions] = useState(null);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);
  // null = not yet initialized from the loaded ticket. Once set, further
  // ticket refetches never clobber an in-progress edit.
  const [draftText, setDraftText] = useState(null);

  useEffect(() => {
    if (ticket && draftText === null) {
      setDraftText(ticket.ai_draft || ticket.ai_response || '');
    }
  }, [ticket, draftText]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    document.title = `Ticket ${ticket_id}: tResolv`;
  }, [ticket_id]);

  const handleSend = () => {
    if (!reply.trim()) return;
    setSendStatus('');
    sendMessage({ id: ticket_id, text: reply }, {
      onSuccess: () => {
        setReply('');
        setSendStatus('Message sent.');
      },
      // This used to only ever set actionStatus, which renders in the
      // Operational Control card in the right panel — nowhere near the
      // compose box or Send button the user is actually looking at. A
      // failure (e.g. "No Gmail connected for this brand") was real but
      // invisible, which looked identical to the button doing nothing.
      onError: (err) => setSendStatus(err.response?.data?.detail || 'Failed to send message.')
    });
  };

  const handleTakeover = () => {
    takeover(ticket_id, {
      onSuccess: () => setActionStatus('Takeover active. AI disabled.'),
      onError: (err) => setActionStatus(err.response?.data?.detail || 'Takeover failed.')
    });
  };

  const handleRelease = () => {
    release(ticket_id, {
      onSuccess: () => setActionStatus('Released to AI. Automations resumed.'),
      onError: (err) => setActionStatus(err.response?.data?.detail || 'Release failed.')
    });
  };

  const handleApproveAI = async () => {
    setApproving(true);
    setActionStatus('');
    try {
      const res = await client.post(`/api/v2/tickets/${ticket_id}/approve-ai`, draftText ? { body: draftText } : {});
      if (res.data?.success) {
        setActionStatus('AI response approved and email sent.');
        setTimeout(() => window.location.reload(), 1500);
      } else {
        setActionStatus(res.data?.error || 'Approved but email could not be sent. Check Gmail connection.');
      }
    } catch (err) {
      setActionStatus(err.response?.data?.detail || err.response?.data?.error || 'Failed to approve AI response.');
    } finally {
      setApproving(false);
    }
  };

  const loadSuggestions = async () => {
    setLoadingSuggestions(true);
    try {
      const res = await client.get(`/api/tickets/${ticket_id}/reply-suggestions`);
      const raw = res.data?.suggestions;
      // The AI can return non-string values (null, a number, or occasionally a
      // nested object like { text: "...", tone: "brief" } instead of a flat
      // string) for any of these fields. String(someObject) does NOT extract
      // its text — it silently stringifies to the literal "[object Object]",
      // which is what was landing in the reply box. Pull out a nested string
      // field if there is one before falling back to plain coercion.
      setSuggestions(raw ? {
        short: extractReplyText(raw.short),
        detailed: extractReplyText(raw.detailed),
        empathetic: extractReplyText(raw.empathetic),
      } : null);
    } catch { setSuggestions(null); }
    finally { setLoadingSuggestions(false); }
  };

  const anyLoading = ticketLoading;

  if (anyLoading && !ticket) {
    return (
      <div style={{ padding: '24px', display: 'flex', gap: '24px' }}>
        <div style={{ flex: '1' }}>
          <div className="skeleton" style={{ height: '80px', borderRadius: '6px', marginBottom: '16px' }} />
          <div className="skeleton" style={{ height: '400px', borderRadius: '6px' }} />
        </div>
      </div>
    );
  }

  if (ticketError?.response?.status === 404 || (!ticket && !anyLoading)) {
    return (
      <div style={{ padding: '48px', textAlign: 'center' }}>
        <div style={{ fontSize: '14px', color: 'var(--text-muted)', marginBottom: '16px' }}>
          This conversation does not exist or you do not have access to it.
        </div>
        <button
          onClick={() => navigate('/tickets')}
          style={{ padding: '8px 16px', fontSize: '13px', borderRadius: '4px', border: '1px solid var(--border)', background: 'var(--bg-primary)', cursor: 'pointer' }}
        >
          ← Back to Conversations
        </button>
      </div>
    );
  }

  // conversation_overrides.active (surfaced by the backend as
  // human_override_active) is the authoritative signal, not ticket.status —
  // sending a manual reply while in Human Mode overwrites status to
  // "resolved" (legitimate ticket-lifecycle behavior, not a takeover
  // release), which used to make this flip back to showing "AI is handling
  // this" even though the override was still active. status is kept as a
  // fallback for tickets fetched before this field existed.
  const isHumanHandled = ticket.human_override_active === true || ticket.status === 'human_managing';

  return (
    <div className="split-panel" style={{ padding: '24px', gap: '24px', alignItems: 'flex-start' }}>

      {/* Left panel — 60% */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '16px', minWidth: 0 }}>

        {/* Back */}
        <button
          onClick={() => navigate('/tickets')}
          style={{ alignSelf: 'flex-start', fontSize: '13px', color: 'var(--text-secondary)', background: 'none', padding: '0', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '4px' }}
        >
          ← Back to Conversations
        </button>

        {/* Customer info */}
        <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', padding: '16px 20px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '8px' }}>
            <div>
              <div style={{ fontWeight: '600', fontSize: '15px', marginBottom: '4px' }}>
                {ticket.customer_email || ticket.customer_name || ticket.sender_id || 'Unknown Sender'}
              </div>
              <div style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>Channel: {ticket.channel || 'email'}</div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '6px' }}>
              <Badge status={ticket.status} size="md" />
              <span style={{ fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'DM Mono, monospace' }}>
                #{String(ticket.id).slice(0, 8)}
              </span>
            </div>
          </div>
        </div>

        {/* Real processing activity — only rendered once the backend has actually written events */}
        <ActivityTimeline events={ticket.events} ticketStatus={ticket.status} />

        {/* Customer feedback — only shown when the customer actually left one */}
        {ticket.feedback && (
          <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', padding: '14px 20px', display: 'flex', gap: '10px', alignItems: 'flex-start' }}>
            <span style={{ fontSize: '18px', lineHeight: 1 }}>{ticket.feedback.rating === 'positive' ? '👍' : '👎'}</span>
            <div>
              <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-primary)' }}>
                Customer feedback: {ticket.feedback.rating === 'positive' ? 'Helpful' : 'Not helpful'}
              </div>
              {ticket.feedback.feedback_text && (
                <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '4px', lineHeight: 1.5 }}>
                  "{ticket.feedback.feedback_text}"
                </div>
              )}
            </div>
          </div>
        )}

        {/* Message thread */}
        <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', overflow: 'hidden' }}>
          <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)' }}>
            Conversation Replay
          </div>
          <div 
            ref={scrollRef}
            style={{ padding: '20px', height: '500px', overflowY: 'auto', display: 'flex', flexDirection: 'column' }}
          >
            {messages.length === 0 ? (
              <div style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '32px' }}>No messages in this thread</div>
            ) : messages.map((msg, i) => (
              <ChatBubble key={i} message={msg} role={msg.role === 'user' ? 'customer' : (msg.role === 'admin' ? 'admin' : 'ai')} />
            ))}
          </div>

          {/* Compose */}
          <div style={{ padding: '16px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: '10px', alignItems: 'flex-end' }}>
            <textarea
              value={reply}
              onChange={e => setReply(e.target.value)}
              placeholder={isHumanHandled ? "Write a response..." : "AI is handling this. Click 'Take Over' to reply manually."}
              rows={3}
              disabled={!isHumanHandled}
              style={{
                flex: 1,
                padding: '10px 12px',
                border: '1px solid var(--border-strong)',
                borderRadius: '4px',
                fontSize: '14px',
                background: isHumanHandled ? 'var(--bg-primary)' : 'var(--bg-tertiary)',
                resize: 'vertical',
                lineHeight: '1.5',
              }}
            />
            <button
              onClick={handleSend}
              disabled={sending || !reply.trim() || !isHumanHandled}
              style={{
                padding: '10px 18px',
                borderRadius: '4px',
                background: reply.trim() && !sending ? 'var(--accent)' : 'var(--bg-tertiary)',
                color: reply.trim() && !sending ? 'white' : 'var(--text-muted)',
                fontWeight: '500',
                fontSize: '13px',
                cursor: reply.trim() && !sending ? 'pointer' : 'not-allowed',
                whiteSpace: 'nowrap',
              }}
            >
              {sending ? 'Sending...' : 'Send'}
            </button>
          </div>
          <Alert
            variant={sendStatus.includes('fail') || sendStatus.includes('No Gmail') ? 'error' : 'success'}
            style={{ margin: '0 20px 16px', fontSize: '12px' }}
          >
            {sendStatus}
          </Alert>
        </div>
      </div>

      {/* Right panel — 40% */}
      <div className="split-panel-side" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

        {/* Lead Summary */}
        <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', padding: '16px 20px' }}>
          <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '14px' }}>Lead Intelligence</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div>
              <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '3px' }}>Current Handler</div>
              <div style={{ fontWeight: '600', color: isHumanHandled ? 'var(--warning)' : 'var(--success)', textTransform: 'capitalize' }}>
                {isHumanHandled ? 'Human' : 'Auto-routing...'}
              </div>
            </div>
            {ticket.unread_count > 0 && (
              <div style={{ padding: '8px', background: 'var(--accent-light)', borderRadius: '4px', fontSize: '12px', color: 'var(--accent)', fontWeight: '600' }}>
                {ticket.unread_count} Unread Messages
              </div>
            )}
          </div>
        </div>

        {/* Control Actions */}
        <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', padding: '16px 20px' }}>
          <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '12px' }}>Operational Control</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {!isHumanHandled ? (
              <button
                onClick={handleTakeover}
                style={{ padding: '9px 14px', borderRadius: '4px', background: 'var(--accent)', color: 'white', fontWeight: '500', fontSize: '13px', textAlign: 'left', cursor: 'pointer' }}
              >
                ✋ Take Over Conversation
              </button>
            ) : (
              <button
                onClick={handleRelease}
                style={{ padding: '9px 14px', borderRadius: '4px', background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontWeight: '600', fontSize: '13px', border: '1px solid var(--border)', textAlign: 'left', cursor: 'pointer' }}
              >
                🤖 Release to AI
              </button>
            )}
          </div>
          <Alert
            variant={actionStatus.includes('fail') || actionStatus.includes('could not') ? 'error' : 'success'}
            style={{ marginTop: '10px', fontSize: '12px' }}
          >
            {actionStatus}
          </Alert>
        </div>

        {/* Order Context */}
        <OrderPanel ticketId={ticket_id} ticket={ticket} />

        {/* Escalation Context — human-readable brief for why this needs a person */}
        {ticket.status === 'escalated' && (() => {
          const brief = buildEscalationBrief(ticket);
          const risk = RISK_COPY[ticket.risk_level] || null;
          return (
            <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--warning, #F59E0B)', borderRadius: '6px', padding: '16px 20px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)' }}>⚠ Escalated: Needs Your Attention</div>
                <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                  {risk && (
                    <span style={{ fontSize: '11px', fontWeight: '600', color: risk.color }}>{risk.label}</span>
                  )}
                  {brief.confidencePct != null && (
                    <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>· {Math.round(brief.confidencePct)}% confidence</span>
                  )}
                </div>
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', fontSize: '13px' }}>
                {brief.customerLine && (
                  <div>
                    <div style={{ fontSize: '11px', fontWeight: '600', color: 'var(--text-muted)', marginBottom: '2px', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Customer wants</div>
                    <div style={{ color: 'var(--text-primary)' }}>{brief.customerLine}</div>
                  </div>
                )}
                <div>
                  <div style={{ fontSize: '11px', fontWeight: '600', color: 'var(--text-muted)', marginBottom: '2px', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Why AI stopped</div>
                  <div style={{ color: 'var(--text-primary)' }}>{brief.whyStopped}</div>
                </div>
                <div>
                  <div style={{ fontSize: '11px', fontWeight: '600', color: 'var(--text-muted)', marginBottom: '2px', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Order context</div>
                  <div style={{ color: 'var(--text-primary)' }}>{brief.orderContext}</div>
                </div>
                <div style={{ padding: '10px', background: 'var(--bg-secondary)', borderRadius: '4px' }}>
                  <div style={{ fontSize: '11px', fontWeight: '600', color: 'var(--text-muted)', marginBottom: '2px', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Recommended action</div>
                  <div style={{ color: 'var(--text-primary)' }}>{brief.recommendedAction}</div>
                </div>
              </div>
            </div>
          );
        })()}

        {/* AI Draft Approval */}
        {(ticket.ai_draft || ticket.ai_response) && ticket.status !== 'resolved' && (
          <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', padding: '16px 20px' }}>
            <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '10px' }}>AI Draft (editable)</div>
            <textarea
              value={draftText ?? (ticket.ai_draft || ticket.ai_response || '')}
              onChange={e => setDraftText(e.target.value)}
              rows={6}
              style={{ width: '100%', fontSize: '13px', color: 'var(--text-primary)', lineHeight: '1.6', marginBottom: '12px', padding: '10px', background: 'var(--bg-secondary)', borderRadius: '4px', border: '1px solid var(--border)', resize: 'vertical', boxSizing: 'border-box', fontFamily: 'inherit' }}
            />
            <button
              onClick={handleApproveAI}
              disabled={approving || !(draftText ?? '').trim()}
              style={{
                width: '100%',
                padding: '9px 14px',
                borderRadius: '4px',
                background: approving ? 'var(--bg-tertiary)' : 'var(--success)',
                color: approving ? 'var(--text-muted)' : 'white',
                fontWeight: '600',
                fontSize: '13px',
                cursor: approving ? 'not-allowed' : 'pointer',
              }}
            >
              {approving ? 'Sending...' : '✓ Approve & Send'}
            </button>
          </div>
        )}

        {/* Smart Reply Suggestions */}
        {(ticket.ai_draft || ticket.ai_reply) && !suggestions && (
          <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', padding: '16px 20px' }}>
            <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '10px' }}>Quick Replies</div>
            <button
              onClick={loadSuggestions}
              disabled={loadingSuggestions}
              style={{ fontSize: '12px', color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', padding: '0' }}
            >
              {loadingSuggestions ? 'Generating...' : 'Generate 3 variations →'}
            </button>
          </div>
        )}
        {suggestions && (
          <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', padding: '16px 20px' }}>
            <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '10px' }}>Quick Replies</div>
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '10px' }}>
              {[['Short', suggestions.short], ['Detailed', suggestions.detailed], ['Empathetic', suggestions.empathetic]].map(([label, text]) => (
                <button
                  key={label}
                  onClick={() => setReply(String(text ?? ''))}
                  style={{ padding: '5px 12px', fontSize: '12px', borderRadius: '12px', border: '1px solid var(--border)', background: 'var(--bg-secondary)', color: 'var(--text-secondary)', cursor: 'pointer', fontWeight: '500' }}
                >
                  {label}
                </button>
              ))}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Click to fill reply box</div>
          </div>
        )}
      </div>
    </div>
  );
}
