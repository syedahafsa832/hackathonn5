import { useEffect, useState, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import client from '../api/client';
import { useBrand } from '../context/BrandContext';
import Badge from '../components/Badge';
import Alert from '../components/Alert';
import { useConversations, useMarkRead } from '../hooks/useApi';

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function Tickets() {
  const navigate = useNavigate();
  const { brand } = useBrand();
  const [statusFilter, setStatusFilter] = useState('active');
  const [search, setSearch] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [gmailConnected, setGmailConnected] = useState(null);

  const { data: tickets = [], isLoading: loading, error: queryError, refetch } = useConversations(statusFilter || 'active', brand?.id);
  const { mutate: markRead } = useMarkRead();
  const [refreshing, setRefreshing] = useState(false);

  // isLoading only reflects the *first* fetch — once data exists, a manual
  // refetch() only flips isFetching, which this page didn't track, so the
  // button gave zero visual feedback and looked broken even though it was
  // actually refetching. Track our own refreshing state instead, same
  // pattern as the working refresh button on the Dashboard overview page.
  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await refetch();
    setRefreshing(false);
  }, [refetch]);

  useEffect(() => {
    document.title = "Conversations: tResolv";
    client.get('/api/v1/settings/gmail/status')
      .then(res => setGmailConnected(!!res.data?.connected))
      // A failed status check (cold start, transient network error) is not proof
      // Gmail is disconnected — leave state as unknown rather than showing the
      // "not connected" banner for a brand that's actually connected.
      .catch(() => {});
  }, []);

  const visibleTickets = tickets;

  const sortedAndFiltered = (() => {
    let result = search
      ? visibleTickets.filter(t =>
          (t.customer_email || t.sender_id || '').toLowerCase().includes(search.toLowerCase()) ||
          (t.subject || '').toLowerCase().includes(search.toLowerCase()) ||
          (t.id || '').toLowerCase().includes(search.toLowerCase())
        )
      : [...visibleTickets];

    if (tagFilter) {
      result = result.filter(t => (t.tags || []).includes(tagFilter));
    }

    // Newest-first by updated_at, same as the Dashboard's Recent Conversations
    // widget — this used to sort by sentiment first (angry/frustrated ahead of
    // everything else), which is why two tickets updated seconds apart could
    // appear far apart in the list depending on sentiment.
    result.sort((a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0));

    return result;
  })();

  // useConversations polls every 10s in the background — a single transient
  // failure (cold start, network blip) shouldn't blank out a page that's
  // still showing perfectly good cached data underneath. Only surface the
  // error when there's nothing on screen to fall back on.
  const error = queryError && tickets.length === 0 ? 'Failed to load conversations. Please try again.' : '';

  const handleOpenConversation = (id) => {
    markRead(id);
    navigate(`/tickets/${id}`);
  };

  return (
    <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '16px' }}>

      {/* Filter bar */}
      <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap', justifyContent: 'space-between' }}>
        <input
          placeholder="Search conversations..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{
            padding: '8px 12px',
            border: '1px solid #E4E4E7',
            borderRadius: '6px',
            fontSize: '14px',
            background: 'white',
            width: '240px',
            transition: 'border-color 0.15s',
            outline: 'none',
          }}
          onFocus={e => e.target.style.borderColor = '#06B6D4'}
          onBlur={e => e.target.style.borderColor = '#E4E4E7'}
        />
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          style={{
            padding: '8px 12px',
            border: '1px solid #E4E4E7',
            borderRadius: '6px',
            fontSize: '14px',
            background: 'white',
            color: '#0F172A',
            cursor: 'pointer',
            transition: 'border-color 0.15s',
            outline: 'none',
          }}
          onFocus={e => e.target.style.borderColor = '#06B6D4'}
          onBlur={e => e.target.style.borderColor = '#E4E4E7'}
        >
          <option value="active">All</option>
          <option value="processing">Processing</option>
          <option value="open">Open</option>
          <option value="escalated">Escalated</option>
          <option value="auto_resolved">Auto-Resolved</option>
          <option value="resolved">Resolved</option>
        </select>
        <select
          value={tagFilter}
          onChange={e => setTagFilter(e.target.value)}
          style={{
            padding: '8px 12px',
            border: '1px solid #E4E4E7',
            borderRadius: '6px',
            fontSize: '14px',
            background: 'white',
            color: '#0F172A',
            cursor: 'pointer',
            transition: 'border-color 0.15s',
            outline: 'none',
          }}
          onFocus={e => e.target.style.borderColor = '#06B6D4'}
          onBlur={e => e.target.style.borderColor = '#E4E4E7'}
        >
          <option value="">All Tags</option>
          {['shipping', 'refund', 'cancel', 'exchange', 'damaged', 'complaint', 'question', 'compliment'].map(t => (
            <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
          ))}
        </select>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginLeft: 'auto' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '12px', color: '#10B981' }}>
            <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: '#10B981', display: 'inline-block', animation: 'pulse 2s ease-in-out infinite' }} />
            Live
          </span>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            style={{
              padding: '7px 14px',
              borderRadius: '6px',
              border: '1px solid #E4E4E7',
              background: 'white',
              fontSize: '13px',
              color: '#475569',
              cursor: refreshing ? 'not-allowed' : 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              opacity: refreshing ? 0.6 : 1,
            }}
          >
            <span style={{ display: 'inline-block', animation: refreshing ? 'spin 0.8s linear infinite' : 'none' }}>↻</span>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Gmail not connected banner */}
      {gmailConnected === false && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', background: '#FAFAFA', border: '1px solid #E4E4E7', borderRadius: '6px', fontSize: '13px' }}>
          <span style={{ color: '#475569' }}>
            Gmail not connected. New emails will not be polled until you connect.
          </span>
          <Link to="/settings" style={{ padding: '5px 12px', borderRadius: '4px', background: '#06B6D4', color: 'white', fontSize: '12px', fontWeight: '600', textDecoration: 'none' }}>
            Connect Gmail →
          </Link>
        </div>
      )}

      <Alert variant="error">{error}</Alert>

      {/* Table */}
      <div style={{ background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', overflow: 'hidden' }}>

        {/* Mobile card list */}
        <div className="table-mobile-cards">
          {loading ? (
            Array.from({ length: 5 }, (_, i) => (
              <div key={i} className="skeleton" style={{ height: '70px', borderRadius: '6px' }} />
            ))
          ) : sortedAndFiltered.length === 0 ? (
            <div style={{ padding: '32px', textAlign: 'center', color: '#94A3B8' }}>No conversations found</div>
          ) : sortedAndFiltered.map(c => (
            <div key={c.id} className="mobile-card" onClick={() => handleOpenConversation(c.id)}>
              <div className="mobile-card-row">
                <span style={{ fontFamily: 'DM Mono, monospace', fontSize: '12px', color: '#64748B' }}>#{String(c.id).slice(0, 8)}</span>
                <span style={{ color: '#64748B' }}>{formatDate(c.updated_at)}</span>
              </div>
              <div style={{ fontWeight: c.unread_count > 0 ? '600' : '500', color: '#0F172A' }}>
                {c.customer_email || c.sender_id || '—'}
              </div>
              <div style={{ fontSize: '12.5px', color: '#64748B', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {c.last_message || '—'}
              </div>
              <div className="mobile-card-row">
                <span style={{ textTransform: 'capitalize' }}>{c.channel}</span>
                <Badge status={c.status} />
              </div>
              {(c.tags || []).length > 0 && (
                <div style={{ display: 'flex', gap: '3px', flexWrap: 'wrap' }}>
                  {(c.tags || []).slice(0, 3).map(tag => (
                    <span key={tag} style={{ fontSize: '10px', padding: '1px 6px', borderRadius: '8px', background: '#F1F5F9', color: '#475569', fontWeight: '500' }}>
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="table-desktop-wrap" style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: '#F8FAFC', position: 'sticky', top: 0 }}>
                {['ID', 'Channel', 'Sender', 'Last Message', 'Status', 'Sentiment', 'Tags', 'Updated'].map(h => (
                  <th key={h} style={{ padding: '10px 16px', textAlign: 'left', fontSize: '11px', fontWeight: '600', color: '#64748B', whiteSpace: 'nowrap', borderBottom: '1px solid #E4E4E7', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 8 }, (_, i) => (
                  <tr key={i} style={{ background: 'transparent' }}>
                    {[80, 80, 140, 160, 80, 60, 200, 100].map((w, j) => (
                      <td key={j} style={{ padding: '12px 16px', borderBottom: '1px solid #F1F5F9', height: '48px' }}>
                        <div className="skeleton" style={{ height: '14px', width: `${w}px` }} />
                      </td>
                    ))}
                  </tr>
                ))
              ) : sortedAndFiltered.length === 0 ? (
                <tr>
                  <td colSpan={8} style={{ padding: '48px', textAlign: 'center', color: '#94A3B8' }}>
                    No conversations found
                  </td>
                </tr>
              ) : sortedAndFiltered.map((c, i) => {
                return (
                  <tr
                    key={c.id}
                    onClick={() => handleOpenConversation(c.id)}
                    style={{
                      cursor: 'pointer',
                      background: c.unread_count > 0 ? '#F0FAFE' : 'transparent',
                      fontWeight: c.unread_count > 0 ? '600' : 'normal',
                      height: '48px',
                      borderBottom: '1px solid #F1F5F9'
                    }}
                    onMouseEnter={e => e.currentTarget.style.background = '#F8FAFC'}
                    onMouseLeave={e => e.currentTarget.style.background = c.unread_count > 0 ? '#F0FAFE' : 'transparent'}
                  >
                    <td style={{ padding: '0 16px', fontFamily: 'DM Mono, monospace', fontSize: '12px', color: '#64748B', whiteSpace: 'nowrap' }}>
                      #{String(c.id).slice(0, 8)}
                    </td>
                    <td style={{ padding: '0 16px', color: '#1E293B', textTransform: 'capitalize' }}>
                      {c.channel}
                    </td>
                    <td style={{ padding: '0 16px', color: '#1E293B' }}>
                      {c.customer_email || c.sender_id || '—'}
                    </td>
                    <td style={{ padding: '0 16px', color: '#64748B', fontSize: '13px', maxWidth: '220px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {c.last_message || '—'}
                    </td>
                    <td style={{ padding: '0 16px' }}>
                      <Badge status={c.status} />
                    </td>
                    <td style={{ padding: '0 16px' }}>
                      {c.customer_sentiment === 'angry' && <span style={{ fontSize: '11px', padding: '2px 7px', borderRadius: '10px', background: '#FEF2F2', color: '#EF4444', fontWeight: '600' }}>Angry</span>}
                      {c.customer_sentiment === 'frustrated' && <span style={{ fontSize: '11px', padding: '2px 7px', borderRadius: '10px', background: '#FFFBEB', color: '#F59E0B', fontWeight: '600' }}>Frustrated</span>}
                      {c.customer_sentiment === 'positive' && <span style={{ fontSize: '11px', padding: '2px 7px', borderRadius: '10px', background: '#ECFDF5', color: '#10B981', fontWeight: '600' }}>Happy</span>}
                    </td>
                    <td style={{ padding: '0 16px' }}>
                      <div style={{ display: 'flex', gap: '3px', flexWrap: 'wrap' }}>
                        {(c.tags || []).slice(0, 2).map(tag => {
                          const tagColors = { refund: '#FEF2F2', cancel: '#FEF2F2', shipping: '#EFF6FF', exchange: '#FFFBEB', damaged: '#FDF2F8', complaint: '#FFEDD5', question: '#F5F3FF', compliment: '#ECFDF5' };
                          const tagTextColors = { refund: '#EF4444', cancel: '#EF4444', shipping: '#2563EB', exchange: '#F59E0B', damaged: '#DB2777', complaint: '#EA580C', question: '#8B5CF6', compliment: '#10B981' };
                          return (
                            <span key={tag} style={{ fontSize: '10px', padding: '1px 6px', borderRadius: '8px', background: tagColors[tag] || '#F1F5F9', color: tagTextColors[tag] || '#475569', fontWeight: '500' }}>
                              {tag}
                            </span>
                          );
                        })}
                      </div>
                    </td>
                    <td style={{ padding: '0 16px', color: '#64748B', fontSize: '12px', fontFamily: 'DM Mono, monospace', whiteSpace: 'nowrap' }}>
                      {formatDate(c.updated_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
