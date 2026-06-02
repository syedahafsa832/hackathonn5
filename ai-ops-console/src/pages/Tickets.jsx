import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import client from '../api/client';
import { useBrand } from '../context/BrandContext';
import Badge from '../components/Badge';
import { useConversations, useMarkRead } from '../hooks/useApi';

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function Tickets() {
  const navigate = useNavigate();
  const { activeBrand } = useBrand();
  const [statusFilter, setStatusFilter] = useState('active');
  const [search, setSearch] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [gmailConnected, setGmailConnected] = useState(null); // null = loading

  const { data: tickets = [], isLoading: loading, error: queryError, refetch } = useConversations(statusFilter || 'active', activeBrand?.id);
  const { mutate: markRead } = useMarkRead();

  useEffect(() => {
    client.get('/api/v1/settings/gmail/status')
      .then(res => setGmailConnected(!!res.data?.connected))
      .catch(() => setGmailConnected(false));
  }, []);

  // Show all tickets regardless of Gmail connection status
  const visibleTickets = tickets;

  const SENTIMENT_ORDER = { angry: 0, frustrated: 1, positive: 3, neutral: 2 };

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

    // Sort: angry first, frustrated second, then by date desc
    result.sort((a, b) => {
      const sa = SENTIMENT_ORDER[a.customer_sentiment] ?? 2;
      const sb = SENTIMENT_ORDER[b.customer_sentiment] ?? 2;
      if (sa !== sb) return sa - sb;
      return new Date(b.updated_at || 0) - new Date(a.updated_at || 0);
    });

    return result;
  })();

  const error = queryError ? 'Failed to load conversations. Please try again.' : '';

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
            border: '1px solid var(--border-strong)',
            borderRadius: '4px',
            fontSize: '14px',
            background: 'var(--bg-primary)',
            width: '240px',
          }}
        />
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          style={{
            padding: '8px 12px',
            border: '1px solid var(--border-strong)',
            borderRadius: '4px',
            fontSize: '14px',
            background: 'var(--bg-primary)',
            color: 'var(--text-primary)',
            cursor: 'pointer',
          }}
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
          style={{ padding: '8px 12px', border: '1px solid var(--border-strong)', borderRadius: '4px', fontSize: '14px', background: 'var(--bg-primary)', color: 'var(--text-primary)', cursor: 'pointer' }}
        >
          <option value="">All Tags</option>
          {['shipping', 'refund', 'cancel', 'exchange', 'damaged', 'complaint', 'question', 'compliment'].map(t => (
            <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
          ))}
        </select>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginLeft: 'auto' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '12px', color: 'var(--success)' }}>
            <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: 'var(--success)', display: 'inline-block', animation: 'pulse 2s ease-in-out infinite' }} />
            Live
          </span>
          <button
            onClick={() => refetch()}
            disabled={loading}
            style={{
              padding: '7px 14px',
              borderRadius: '4px',
              border: '1px solid var(--border)',
              background: 'var(--bg-primary)',
              fontSize: '13px',
              color: 'var(--text-secondary)',
              cursor: loading ? 'not-allowed' : 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            {loading ? '↻' : '↻'} Refresh
          </button>
        </div>
      </div>

      {/* Gmail not connected banner */}
      {gmailConnected === false && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: '6px', fontSize: '13px' }}>
          <span style={{ color: 'var(--text-secondary)' }}>
            Gmail not connected — new emails will not be polled until you connect.
          </span>
          <Link to="/settings" style={{ padding: '5px 12px', borderRadius: '4px', background: 'var(--accent)', color: 'white', fontSize: '12px', fontWeight: '600', textDecoration: 'none' }}>
            Connect Gmail →
          </Link>
        </div>
      )}

      {/* Table */}
      <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', overflow: 'hidden' }}>
        {error && (
          <div style={{ padding: '16px 20px', color: 'var(--danger)', background: 'var(--danger-light)', borderBottom: '1px solid var(--border)' }}>
            {error}
          </div>
        )}

        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--bg-secondary)', position: 'sticky', top: 0 }}>
                {['ID', 'Channel', 'Sender', 'Status', 'Sentiment', 'Tags', 'Updated'].map(h => (
                  <th key={h} style={{ padding: '10px 16px', textAlign: 'left', fontSize: '12px', fontWeight: '600', color: 'var(--text-muted)', whiteSpace: 'nowrap', borderBottom: '1px solid var(--border)' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 8 }, (_, i) => (
                  <tr key={i} style={{ background: i % 2 === 1 ? 'var(--bg-secondary)' : 'var(--bg-primary)' }}>
                    {[80, 80, 140, 80, 60, 200, 100].map((w, j) => (
                      <td key={j} style={{ padding: '12px 16px' }}>
                        <div className="skeleton" style={{ height: '14px', width: `${w}px` }} />
                      </td>
                    ))}
                  </tr>
                ))
              ) : sortedAndFiltered.length === 0 ? (
                <tr>
                  <td colSpan={7} style={{ padding: '48px', textAlign: 'center', color: 'var(--text-muted)' }}>
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
                      background: c.unread_count > 0 ? 'var(--accent-light)' : (i % 2 === 1 ? 'var(--bg-secondary)' : 'var(--bg-primary)'),
                      fontWeight: c.unread_count > 0 ? '600' : 'normal'
                    }}
                    onMouseEnter={e => e.currentTarget.style.background = 'var(--accent-light)'}
                    onMouseLeave={e => e.currentTarget.style.background = c.unread_count > 0 ? 'var(--accent-light)' : (i % 2 === 1 ? 'var(--bg-secondary)' : 'var(--bg-primary)')}
                  >
                    <td style={{ padding: '10px 16px', fontFamily: 'DM Mono, monospace', fontSize: '12px', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                      #{String(c.id).slice(0, 8)}
                    </td>
                    <td style={{ padding: '10px 16px', color: 'var(--text-primary)', textTransform: 'capitalize' }}>
                      {c.channel}
                    </td>
                    <td style={{ padding: '10px 16px', color: 'var(--text-primary)' }}>
                      {c.customer_email || c.sender_id || '—'}
                    </td>
                    <td style={{ padding: '10px 16px' }}>
                      <Badge status={c.status} />
                    </td>
                    <td style={{ padding: '10px 16px' }}>
                      {c.customer_sentiment === 'angry' && <span style={{ fontSize: '11px', padding: '2px 7px', borderRadius: '10px', background: '#fee2e2', color: '#dc2626', fontWeight: '600' }}>Angry</span>}
                      {c.customer_sentiment === 'frustrated' && <span style={{ fontSize: '11px', padding: '2px 7px', borderRadius: '10px', background: '#fef3c7', color: '#d97706', fontWeight: '600' }}>Frustrated</span>}
                      {c.customer_sentiment === 'positive' && <span style={{ fontSize: '11px', padding: '2px 7px', borderRadius: '10px', background: '#dcfce7', color: '#16a34a', fontWeight: '600' }}>Happy</span>}
                    </td>
                    <td style={{ padding: '10px 16px' }}>
                      <div style={{ display: 'flex', gap: '3px', flexWrap: 'wrap' }}>
                        {(c.tags || []).slice(0, 2).map(tag => {
                          const tagColors = { refund: '#fee2e2', cancel: '#fee2e2', shipping: '#dbeafe', exchange: '#fef3c7', damaged: '#fce7f3', complaint: '#fed7aa', question: '#ede9fe', compliment: '#dcfce7' };
                          const tagTextColors = { refund: '#dc2626', cancel: '#dc2626', shipping: '#1d4ed8', exchange: '#d97706', damaged: '#be185d', complaint: '#c2410c', question: '#7c3aed', compliment: '#16a34a' };
                          return (
                            <span key={tag} style={{ fontSize: '10px', padding: '1px 6px', borderRadius: '8px', background: tagColors[tag] || '#f3f4f6', color: tagTextColors[tag] || '#374151', fontWeight: '500' }}>
                              {tag}
                            </span>
                          );
                        })}
                      </div>
                    </td>
                    <td style={{ padding: '10px 16px', color: 'var(--text-muted)', fontSize: '12px', fontFamily: 'DM Mono, monospace', whiteSpace: 'nowrap' }}>
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
