import { useEffect, useState, useRef, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import client from '../api/client';
import StatCard from '../components/StatCard';
import ActionCard from '../components/ActionCard';
import FilteredEmailsWidget from '../components/FilteredEmailsWidget';
import PlanUsageWidget from '../components/PlanUsageWidget';
import { useNotifications } from '../hooks/useNotifications';
import { useStats, useConversations } from '../hooks/useApi';

function formatTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
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
  const [slowLoad, setSlowLoad] = useState(false);
  const [activeBrand, setActiveBrand] = useState(null);
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
  const recentConversations = conversations?.slice(0, 3) || [];

  // Request notification permission on mount and check brands for onboarding
  useEffect(() => {
    if (isSupported && !hasBeenAsked()) {
      setShowNotifBanner(true);
    }
    client.get('/api/brands').then(res => {
      const brands = res.data?.brands || (Array.isArray(res.data) ? res.data : []);
      if (brands.length === 0) {
        navigate('/onboarding');
      } else {
        setActiveBrand(brands[0]);
      }
    }).catch(() => {
      // brands check failed — stay on dashboard
    });
  }, []);

  // Show a "waking up the server" message if loading takes more than 3s (Render free-tier cold start)
  useEffect(() => {
    if (!loading) {
      setSlowLoad(false);
      return;
    }
    const timer = setTimeout(() => setSlowLoad(true), 3000);
    return () => clearTimeout(timer);
  }, [loading]);

  // Update document title with pending count
  useEffect(() => {
    const pending = stats?.escalatedChats ?? 0;
    document.title = pending > 0 ? `(${pending}) Dashboard — tResolv` : 'Dashboard — tResolv';
    return () => { document.title = 'Dashboard — tResolv'; };
  }, [stats?.escalatedChats]);

  // Notify on new active tickets
  useEffect(() => {
    const count = stats?.activeConversations ?? 0;
    if (count > prevTicketCount.current && prevTicketCount.current > 0) {
      notify('New Ticket', `You have ${count} active tickets`);
    }
    prevTicketCount.current = count;
  }, [stats?.activeConversations]);

  const gmailConnected = !!activeBrand?.gmail_connected;
  const shopifyConnected = !!activeBrand?.shopify_connected || !!activeBrand?.shopify_domain;

  return (
    <div style={{ padding: '28px 32px', display: 'flex', flexDirection: 'column', gap: '28px' }}>

      {/* Setup incomplete banner */}
      {activeBrand && (!gmailConnected || !shopifyConnected) && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', background: '#FFFBEB', border: '1px solid #FDE68A', borderRadius: '6px', fontSize: '13px', flexWrap: 'wrap', gap: '8px' }}>
          <div>
            <span style={{ color: '#B45309', fontWeight: '600' }}>Setup incomplete.</span>
            <span style={{ color: '#64748B', marginLeft: '6px' }}>
              {!gmailConnected && 'Gmail not connected. '}
              {!shopifyConnected && 'Shopify not connected. '}
              Luna can't work until both are connected.
            </span>
          </div>
          <Link to="/onboarding" style={{ color: '#B45309', fontSize: '13px', fontWeight: '600', textDecoration: 'none', flexShrink: 0 }}>
            Complete setup →
          </Link>
        </div>
      )}

      {/* Cold-start notice */}
      {loading && slowLoad && (
        <div style={{ textAlign: 'center', fontSize: '13px', color: '#94A3B8', padding: '8px' }}>
          Starting up the server — this takes about 20 seconds on first load. Hang tight...
        </div>
      )}

      {/* Notification permission banner */}
      {showNotifBanner && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', background: '#ECFEFF', border: '1px solid #06B6D4', borderRadius: '6px', fontSize: '13px' }}>
          <span style={{ color: '#0F172A' }}>Enable desktop notifications to be alerted when new tickets arrive.</span>
          <div style={{ display: 'flex', gap: '8px', flexShrink: 0 }}>
            <button
              onClick={async () => { await requestPermission(); setShowNotifBanner(false); }}
              style={{ padding: '5px 12px', borderRadius: '4px', background: '#06B6D4', color: 'white', fontSize: '12px', fontWeight: '600', cursor: 'pointer' }}
            >
              Enable
            </button>
            <button
              onClick={() => { localStorage.setItem('resolv_notifications', 'dismissed'); setShowNotifBanner(false); }}
              style={{ padding: '5px 12px', borderRadius: '4px', border: '1px solid #E4E4E7', background: 'transparent', fontSize: '12px', cursor: 'pointer', color: '#475569' }}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      <PlanUsageWidget />

      {/* Content header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0, fontSize: '18px', fontWeight: '600', color: '#0F172A' }}>Overview</h2>
        <span style={{ fontSize: '13px', color: '#94A3B8' }}>
          {secondsAgo === 0 ? 'Just updated' : `Updated ${secondsAgo < 60 ? `${secondsAgo}s` : `${Math.floor(secondsAgo / 60)}m ${secondsAgo % 60}s`} ago`}
          {' · '}
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: '4px',
              padding: 0, background: 'transparent', border: 'none',
              cursor: refreshing ? 'not-allowed' : 'pointer',
              fontSize: '13px', fontWeight: '500', color: '#06B6D4',
              opacity: refreshing ? 0.6 : 1,
              fontFamily: 'inherit',
            }}
          >
            <span style={{ display: 'inline-block', animation: refreshing ? 'spin 0.8s linear infinite' : 'none' }}>↻</span>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </span>
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
          isAi={true}
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

      {/* Recent Conversations + Filtered Emails — side by side, stacks below 768px */}
      <div className="dashboard-two-col">
        <section style={{ border: '1px solid #E4E4E7', borderRadius: '8px', background: 'white', padding: '24px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h2 style={{ margin: 0, fontSize: '15px', fontWeight: '600', color: '#0F172A' }}>Recent Conversations</h2>
            <Link to="/tickets" style={{ fontSize: '13px', color: '#06B6D4', fontWeight: '500', textDecoration: 'none' }} onMouseEnter={e => e.target.style.textDecoration = 'underline'} onMouseLeave={e => e.target.style.textDecoration = 'none'}>View all →</Link>
          </div>

          {!loading && recentConversations.length === 0 && !gmailConnected ? (
            <div style={{ padding: '32px 8px', textAlign: 'center' }}>
              <div style={{ fontSize: '32px', marginBottom: '10px' }}>📭</div>
              <div style={{ fontSize: '14px', fontWeight: '600', color: '#0F172A', marginBottom: '6px' }}>No conversations yet</div>
              <p style={{ fontSize: '13px', color: '#94A3B8', marginBottom: '14px' }}>
                Connect your Gmail to start receiving and resolving support emails.
              </p>
              <Link to="/onboarding" style={{ display: 'inline-block', padding: '8px 18px', background: '#06B6D4', color: 'white', borderRadius: '6px', textDecoration: 'none', fontSize: '13px', fontWeight: '600' }}>
                Connect Gmail →
              </Link>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {loading ? (
                [1, 2, 3].map(i => (
                  <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 14px', border: '1px solid #F1F5F9', borderRadius: '6px' }}>
                    <div style={{ flex: 1 }}>
                      <div className="skeleton" style={{ height: '13px', width: '140px', marginBottom: '6px' }} />
                      <div className="skeleton" style={{ height: '12px', width: '200px' }} />
                    </div>
                    <div className="skeleton" style={{ height: '12px', width: '50px' }} />
                  </div>
                ))
              ) : recentConversations.length === 0 ? (
                <div style={{ padding: '24px', textAlign: 'center', color: '#94A3B8', fontSize: '13px' }}>No conversations yet</div>
              ) : recentConversations.map(c => (
                <div
                  key={c.id}
                  onClick={() => navigate(`/tickets/${c.id}`)}
                  style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 14px', border: '1px solid #F1F5F9', borderRadius: '6px', cursor: 'pointer' }}
                  onMouseEnter={e => e.currentTarget.style.background = '#F8FAFC'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: '13px', fontWeight: '500', color: '#0F172A' }}>{c.customer_email || c.sender_id || '—'}</div>
                    <div style={{ fontSize: '12px', color: '#94A3B8', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.last_message || '—'}</div>
                  </div>
                  <span style={{ fontFamily: 'DM Mono, monospace', fontSize: '12px', color: '#64748B', flexShrink: 0, marginLeft: '12px' }}>{formatTime(c.updated_at)}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        <FilteredEmailsWidget />
      </div>

      {/* Escalation Queue */}
      <section>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <h2 style={{ margin: 0, fontSize: '15px', fontWeight: '600', color: '#0F172A' }}>Escalation Queue</h2>
          <Link to="/actions" style={{ fontSize: '13px', color: '#06B6D4', fontWeight: '500', textDecoration: 'none' }} onMouseEnter={e => e.target.style.textDecoration = 'underline'} onMouseLeave={e => e.target.style.textDecoration = 'none'}>View queue →</Link>
        </div>

        {loading ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {[1,2,3].map(i => <div key={i} className="skeleton" style={{ height: '100px', borderRadius: '6px' }} />)}
          </div>
        ) : (stats?.escalatedChats ?? 0) === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', border: '1px solid #E4E4E7', borderRadius: '8px', background: 'white', color: '#64748B', fontSize: '14px' }}>
            Escalation queue is empty ✓
          </div>
        ) : (
          <div style={{ padding: '32px', background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '16px' }}>
            <span style={{ fontSize: '14px', color: '#475569' }}>
              There are <strong>{stats.escalatedChats}</strong> conversations awaiting human intervention.
            </span>
            <Link to="/actions" style={{ padding: '10px 20px', background: '#06B6D4', color: 'white', borderRadius: '6px', textDecoration: 'none', fontSize: '14px', fontWeight: '600' }}>Review Escalations</Link>
          </div>
        )}
      </section>
    </div>
  );
}
