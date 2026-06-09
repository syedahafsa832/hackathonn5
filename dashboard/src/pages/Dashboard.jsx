import { useEffect, useState, useRef, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import client from '../api/client';
import StatCard from '../components/StatCard';
import Badge from '../components/Badge';
import ActionCard from '../components/ActionCard';
import FilteredEmailsWidget from '../components/FilteredEmailsWidget';
import { useNotifications } from '../hooks/useNotifications';
import { useStats, useConversations } from '../hooks/useApi';

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function SkeletonRow() {
  return (
    <tr>
      {[1,2,3,4,5].map(i => (
        <td key={i} style={{ padding: '12px 16px' }}>
          <div className="skeleton" style={{ height: '14px', width: i === 3 ? '140px' : '80px' }} />
        </td>
      ))}
    </tr>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const { data: stats, isLoading: statsLoading, refetch: refetchStats } = useStats();
  const { data: conversations, isLoading: convsLoading, refetch: refetchConvs } = useConversations('active');
  const { notify, requestPermission, hasBeenAsked, isSupported } = useNotifications();
  const [showNotifBanner, setShowNotifBanner] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState(new Date());
  const [secondsAgo, setSecondsAgo] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const prevTicketCount = useRef(0);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await Promise.all([refetchStats(), refetchConvs()]);
    setLastRefreshed(new Date());
    setSecondsAgo(0);
    setRefreshing(false);
  }, [refetchStats, refetchConvs]);

  // Live "X seconds ago" counter
  useEffect(() => {
    setSecondsAgo(0);
    const interval = setInterval(() => setSecondsAgo(s => s + 1), 1000);
    return () => clearInterval(interval);
  }, [lastRefreshed]);

  const loading = statsLoading || convsLoading;
  const recentConversations = conversations?.slice(0, 5) || [];

  // Request notification permission on mount and check brands for onboarding
  useEffect(() => {
    if (isSupported && !hasBeenAsked()) {
      setShowNotifBanner(true);
    }
    client.get('/api/brands').then(res => {
      const brands = res.data?.brands || (Array.isArray(res.data) ? res.data : []);
      if (brands.length === 0) {
        navigate('/onboarding');
      }
    }).catch(() => {
      // brands check failed — stay on dashboard
    });
  }, []);

  // Update document title with pending count
  useEffect(() => {
    const pending = stats?.escalatedChats ?? 0;
    document.title = pending > 0 ? `(${pending}) Resolv` : 'Resolv';
    return () => { document.title = 'Resolv'; };
  }, [stats?.escalatedChats]);

  // Notify on new active tickets
  useEffect(() => {
    const count = stats?.activeConversations ?? 0;
    if (count > prevTicketCount.current && prevTicketCount.current > 0) {
      notify('New Ticket', `You have ${count} active tickets`);
    }
    prevTicketCount.current = count;
  }, [stats?.activeConversations]);

  return (
    <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '24px' }}>

      {/* Notification permission banner */}
      {showNotifBanner && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', background: 'var(--accent-light)', border: '1px solid var(--accent)', borderRadius: '6px', fontSize: '13px' }}>
          <span style={{ color: 'var(--text-primary)' }}>Enable desktop notifications to be alerted when new tickets arrive.</span>
          <div style={{ display: 'flex', gap: '8px', flexShrink: 0 }}>
            <button
              onClick={async () => { await requestPermission(); setShowNotifBanner(false); }}
              style={{ padding: '5px 12px', borderRadius: '4px', background: 'var(--accent)', color: 'white', fontSize: '12px', fontWeight: '600', cursor: 'pointer' }}
            >
              Enable
            </button>
            <button
              onClick={() => { localStorage.setItem('resolv_notifications', 'dismissed'); setShowNotifBanner(false); }}
              style={{ padding: '5px 12px', borderRadius: '4px', border: '1px solid var(--border)', background: 'transparent', fontSize: '12px', cursor: 'pointer', color: 'var(--text-secondary)' }}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Stat cards header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h1 style={{ fontSize: '16px', fontWeight: '700', color: 'var(--text-primary)' }}>Dashboard</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            {secondsAgo === 0 ? 'Just updated' : `Updated ${secondsAgo < 60 ? `${secondsAgo}s` : `${Math.floor(secondsAgo / 60)}m ${secondsAgo % 60}s`} ago`}
          </span>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            style={{
              display: 'flex', alignItems: 'center', gap: '5px',
              padding: '5px 12px', borderRadius: '5px',
              background: 'var(--bg-secondary)', border: '1px solid var(--border)',
              cursor: refreshing ? 'not-allowed' : 'pointer',
              fontSize: '12px', fontWeight: '500', color: 'var(--text-secondary)',
              opacity: refreshing ? 0.6 : 1,
            }}
          >
            <span style={{ display: 'inline-block', animation: refreshing ? 'spin 0.8s linear infinite' : 'none' }}>↻</span>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Stat cards */}
      <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
        <StatCard 
          label="Active Conversations" 
          value={stats?.activeConversations ?? 0} 
          loading={loading} 
          subtitle="Real-time open chats" 
        />
        <StatCard
          label="AI Responded"
          value={stats?.aiHandledPct != null ? `${stats?.aiHandledPct}%` : '—'}
          loading={loading}
          subtitle="Replies sent by AI automatically"
        />
        <StatCard 
          label="Escalated Chats" 
          value={stats?.escalatedChats ?? 0} 
          loading={loading} 
          subtitle="Need immediate attention" 
        />
        <StatCard
          label="Pending Approvals"
          value={stats?.pendingApprovals ?? 0}
          loading={loading}
          subtitle="Actions awaiting review"
        />
        <StatCard
          label="Avg Response Time"
          value={(() => {
            const s = stats?.avgResponseSeconds;
            if (s == null) return '—';
            if (s < 60) return `${s}s`;
            return `${Math.floor(s / 60)}m ${s % 60}s`;
          })()}
          loading={loading}
          subtitle="Time to first AI reply (7d)"
        />
      </div>

      {/* Filtered Emails widget */}
      <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
        <FilteredEmailsWidget />
      </div>

      {/* Recent conversations */}
      <section style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', overflow: 'hidden' }}>
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: '14px', fontWeight: '600', color: 'var(--text-primary)' }}>Recent Conversations</h2>
          <Link to="/tickets" style={{ fontSize: '13px', color: 'var(--accent)', fontWeight: '500' }}>View all →</Link>
        </div>

        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--bg-secondary)', position: 'sticky', top: 0 }}>
                {['ID', 'Channel', 'Sender', 'Last Message', 'Updated'].map(h => (
                  <th key={h} style={{ padding: '10px 16px', textAlign: 'left', fontSize: '12px', fontWeight: '600', color: 'var(--text-muted)', whiteSpace: 'nowrap', borderBottom: '1px solid var(--border)' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                [1,2,3,4,5].map(i => <SkeletonRow key={i} />)
              ) : recentConversations.length === 0 ? (
                <tr><td colSpan={5} style={{ padding: '32px', textAlign: 'center', color: 'var(--text-muted)' }}>No conversations yet</td></tr>
              ) : recentConversations.map((c, i) => {
                return (
                  <tr key={c.id}
                    onClick={() => navigate(`/tickets/${c.id}`)}
                    style={{ cursor: 'pointer', background: i % 2 === 1 ? 'var(--bg-secondary)' : 'var(--bg-primary)' }}
                    onMouseEnter={e => e.currentTarget.style.background = 'var(--accent-light)'}
                    onMouseLeave={e => e.currentTarget.style.background = i % 2 === 1 ? 'var(--bg-secondary)' : 'var(--bg-primary)'}
                  >
                    <td style={{ padding: '10px 16px', fontFamily: 'DM Mono, monospace', fontSize: '12px', color: 'var(--text-muted)' }}>#{String(c.id).slice(0, 8)}</td>
                    <td style={{ padding: '10px 16px' }}>
                      {c.channel === 'chat'
                        ? <Badge status="chat" />
                        : <span style={{ textTransform: 'capitalize', fontSize: '13px', color: 'var(--text-secondary)' }}>{c.channel || 'email'}</span>}
                    </td>
                    <td style={{ padding: '10px 16px' }}>{c.customer_email || c.sender_id || '—'}</td>
                    <td style={{ padding: '10px 16px', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.last_message || '—'}</td>
                    <td style={{ padding: '10px 16px', color: 'var(--text-muted)', fontSize: '13px', fontFamily: 'DM Mono, monospace' }}>{formatDate(c.updated_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Escalations summary */}
      <section>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <h2 style={{ fontSize: '14px', fontWeight: '600', color: 'var(--text-primary)' }}>Escalation Queue</h2>
          <Link to="/actions" style={{ fontSize: '13px', color: 'var(--accent)', fontWeight: '500' }}>View queue →</Link>
        </div>

        {loading ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {[1,2,3].map(i => <div key={i} className="skeleton" style={{ height: '100px', borderRadius: '6px' }} />)}
          </div>
        ) : (stats?.escalatedChats ?? 0) === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', border: '1px solid var(--border)', borderRadius: '6px', background: 'var(--bg-primary)', color: 'var(--text-muted)', fontSize: '14px' }}>
            Escalation queue is empty ✓
          </div>
        ) : (
          <div style={{ padding: '20px', background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', textAlign: 'center' }}>
            <span style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>
              There are <strong>{stats.escalatedChats}</strong> conversations awaiting human intervention.
            </span>
            <div style={{ marginTop: '12px' }}>
              <Link to="/actions" style={{ padding: '8px 16px', background: 'var(--accent)', color: 'white', borderRadius: '4px', textDecoration: 'none', fontSize: '13px', fontWeight: '500' }}>Review Escalations</Link>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
