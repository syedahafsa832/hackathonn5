import { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import client from '../api/client';
import Badge from '../components/Badge';
import ActionCard from '../components/ActionCard';
import { useMessages, useSendMessage, useTakeover, useRelease, useConversations, useTicket, useMarkRead } from '../hooks/useApi';

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
        ? ' Confirmation email could not be sent — check Gmail connection.'
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
            </div>
          </div>
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

          {actionResult && (
            <div style={{
              padding: '8px 10px', borderRadius: '4px', fontSize: '12px', lineHeight: '1.5',
              background: actionResult.ok ? 'var(--success-light)' : 'var(--danger-light)',
              color: actionResult.ok ? 'var(--success)' : 'var(--danger)',
            }}>
              {actionResult.msg}
            </div>
          )}

          {(() => {
            const isRestocked = !!order.cancelled_at && order.fulfillment_status === 'restocked';
            return isRestocked ? (
              <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-muted)', padding: '8px 10px', background: 'rgba(255,255,255,0.03)', borderRadius: '6px', borderTop: '1px solid var(--border)', paddingTop: '10px' }}>
                Order was cancelled and inventory restocked. Cannot be restored — customer must place a new order.
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

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function cleanEmailBody(raw) {
  if (!raw) return '';
  // Decode common HTML entities
  let text = raw
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

export default function TicketDetail() {
  const { ticket_id } = useParams();
  const navigate = useNavigate();
  const scrollRef = useRef(null);
  
  const { data: messages = [], isLoading, error: queryError } = useMessages(ticket_id);
  const { data: ticketDirect, isLoading: ticketLoading, error: ticketError } = useTicket(ticket_id);
  const { data: conversations, isLoading: convLoading } = useConversations('active');
  // Prefer direct fetch; fall back to conversations list (covers non-active statuses)
  const ticket = ticketDirect || conversations?.find(c => String(c.id) === String(ticket_id));
  
  const { mutate: sendMessage, isLoading: sending } = useSendMessage();
  const { mutate: takeover } = useTakeover();
  const { mutate: release } = useRelease();
  const [reply, setReply] = useState('');
  const [actionStatus, setActionStatus] = useState('');
  const [approving, setApproving] = useState(false);
  const [suggestions, setSuggestions] = useState(null);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    document.title = `Ticket ${ticket_id} — tResolv`;
  }, [ticket_id]);

  const handleSend = () => {
    if (!reply.trim()) return;
    sendMessage({ id: ticket_id, text: reply }, {
      onSuccess: () => {
        setReply('');
        setActionStatus('Message sent.');
      },
      onError: (err) => setActionStatus(err.response?.data?.detail || 'Failed to send message.')
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
      const res = await client.post(`/api/v2/tickets/${ticket_id}/approve-ai`);
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
      setSuggestions(res.data?.suggestions || null);
    } catch { setSuggestions(null); }
    finally { setLoadingSuggestions(false); }
  };

  const anyLoading = isLoading || ticketLoading || convLoading;

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

  if (queryError && !messages.length) {
    return (
      <div style={{ padding: '48px', textAlign: 'center', color: 'var(--danger)' }}>
        Failed to load conversation history.
        <div style={{ marginTop: '12px' }}>
          <button onClick={() => navigate('/tickets')} style={{ padding: '8px 16px', borderRadius: '4px', border: '1px solid var(--border)', background: 'var(--bg-primary)', cursor: 'pointer' }}>
            ← Back to Conversations
          </button>
        </div>
      </div>
    );
  }

  const isHumanHandled = ticket.handler && ticket.handler.startsWith('human');

  return (
    <div style={{ padding: '24px', display: 'flex', gap: '24px', alignItems: 'flex-start' }}>

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
        </div>
      </div>

      {/* Right panel — 40% */}
      <div style={{ width: '320px', flexShrink: 0, display: 'flex', flexDirection: 'column', gap: '16px' }}>

        {/* Lead Summary */}
        <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', padding: '16px 20px' }}>
          <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '14px' }}>Lead Intelligence</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div>
              <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '3px' }}>Current Handler</div>
              <div style={{ fontWeight: '600', color: isHumanHandled ? 'var(--warning)' : 'var(--success)', textTransform: 'capitalize' }}>
                {ticket.handler || 'Auto-routing...'}
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
          {actionStatus && (
            <div style={{ marginTop: '10px', fontSize: '12px', color: actionStatus.includes('fail') || actionStatus.includes('could not') ? 'var(--danger)' : 'var(--success)', padding: '8px 10px', background: actionStatus.includes('fail') || actionStatus.includes('could not') ? 'var(--danger-light)' : 'var(--success-light)', borderRadius: '4px' }}>
              {actionStatus}
            </div>
          )}
        </div>

        {/* Order Context */}
        <OrderPanel ticketId={ticket_id} ticket={ticket} />

        {/* AI Draft Approval */}
        {(ticket.ai_draft || ticket.ai_response) && ticket.status !== 'resolved' && (
          <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', padding: '16px 20px' }}>
            <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '10px' }}>AI Draft</div>
            <div style={{ fontSize: '13px', color: 'var(--text-primary)', lineHeight: '1.6', marginBottom: '12px', padding: '10px', background: 'var(--bg-secondary)', borderRadius: '4px', whiteSpace: 'pre-wrap' }}>
              {ticket.ai_draft || ticket.ai_response}
            </div>
            <button
              onClick={handleApproveAI}
              disabled={approving}
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
                  onClick={() => setReply(text || '')}
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
