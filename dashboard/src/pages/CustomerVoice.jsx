import { useNavigate } from 'react-router-dom';
import { Star } from 'lucide-react';
import { useBrand } from '../context/BrandContext';
import { useBrandFeedback, useBrandAnalytics } from '../hooks/useApi';
import StatCard from '../components/StatCard';

function formatDate(iso) {
  if (!iso) return '-';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function formatDuration(seconds) {
  if (seconds == null) return null;
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

function StarRow({ count, size = 14 }) {
  return (
    <span style={{ display: 'inline-flex', gap: '1px', verticalAlign: 'middle' }}>
      {Array.from({ length: 5 }).map((_, i) => (
        <Star key={i} size={size} fill={i < count ? '#F59E0B' : 'none'} color={i < count ? '#F59E0B' : '#D4D4D8'} />
      ))}
    </span>
  );
}

function BreakdownBar({ breakdown, total }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxWidth: '320px' }}>
      {[5, 4, 3, 2, 1].map(n => {
        const count = breakdown[String(n)] ?? breakdown[n] ?? 0;
        const pct = total > 0 ? Math.round((count / total) * 100) : 0;
        return (
          <div key={n} style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12.5px', color: '#64748B' }}>
            <span style={{ width: '34px', flexShrink: 0 }}>{n} ★</span>
            <div style={{ flex: 1, height: '6px', borderRadius: '999px', background: '#F1F5F9', overflow: 'hidden' }}>
              <div style={{ width: `${pct}%`, height: '100%', borderRadius: '999px', background: '#F59E0B' }} />
            </div>
            <span style={{ width: '22px', textAlign: 'right', flexShrink: 0 }}>{count}</span>
          </div>
        );
      })}
    </div>
  );
}

const channelLabel = { chat: 'Chat', email: 'Email', unknown: null };

export default function CustomerVoice() {
  const navigate = useNavigate();
  const { brand } = useBrand();
  const { data: feedbackResponse, isLoading: feedbackLoading } = useBrandFeedback(brand?.id);
  const { data: analytics, isLoading: analyticsLoading } = useBrandAnalytics(brand?.id);

  const feedback = feedbackResponse?.feedback || [];
  const summary = feedbackResponse?.summary;

  return (
    <div style={{ padding: '28px 32px', display: 'flex', flexDirection: 'column', gap: '32px' }}>
      <div>
        <h1 style={{ margin: '0 0 4px', fontSize: '20px', fontWeight: '700', color: '#0F172A' }}>Customer Voice</h1>
        <p style={{ margin: 0, fontSize: '13px', color: '#94A3B8' }}>What customers actually say after Luna helps them.</p>
      </div>

      {/* Rating summary */}
      <section>
        {feedbackLoading ? (
          <div className="skeleton" style={{ height: '80px', borderRadius: '8px' }} />
        ) : !summary ? (
          <div style={{ padding: '40px 24px', textAlign: 'center', border: '1px solid #E4E4E7', borderRadius: '8px', background: 'white' }}>
            <div style={{ fontSize: '28px', marginBottom: '8px' }}>💬</div>
            <div style={{ fontSize: '14px', fontWeight: '600', color: '#0F172A', marginBottom: '4px' }}>No ratings yet</div>
            <p style={{ fontSize: '13px', color: '#94A3B8', margin: 0 }}>
              Star ratings and comments customers leave after a conversation will show up here.
            </p>
          </div>
        ) : (
          <div style={{ display: 'flex', gap: '32px', flexWrap: 'wrap', alignItems: 'flex-start', padding: '24px', border: '1px solid #E4E4E7', borderRadius: '8px', background: 'white' }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
                <span style={{ fontFamily: 'DM Mono, monospace', fontSize: '32px', fontWeight: '700', color: '#0F172A' }}>{summary.average}</span>
                <StarRow count={Math.round(summary.average)} size={18} />
              </div>
              <div style={{ fontSize: '13px', color: '#94A3B8', marginTop: '2px' }}>{summary.total} response{summary.total === 1 ? '' : 's'}</div>
            </div>
            <BreakdownBar breakdown={summary.breakdown} total={summary.total} />
          </div>
        )}
      </section>

      {/* Recent feedback cards */}
      {feedback.length > 0 && (
        <section>
          <h2 style={{ margin: '0 0 12px', fontSize: '15px', fontWeight: '600', color: '#0F172A' }}>Recent Feedback</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {feedback.map(f => (
              <div key={f.id} style={{ padding: '16px 18px', border: '1px solid #E4E4E7', borderRadius: '8px', background: 'white' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px', flexWrap: 'wrap', gap: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    {f.rating_stars ? <StarRow count={f.rating_stars} /> : (
                      <span style={{ fontSize: '15px' }}>{f.rating === 'positive' ? '👍' : '👎'}</span>
                    )}
                    {channelLabel[f.channel] && (
                      <span style={{ fontSize: '10.5px', fontWeight: '600', color: '#64748B', background: '#F8FAFC', border: '1px solid #E4E4E7', borderRadius: '999px', padding: '2px 8px' }}>
                        {channelLabel[f.channel]}
                      </span>
                    )}
                  </div>
                  <span style={{ fontSize: '11.5px', color: '#94A3B8', fontFamily: 'DM Mono, monospace' }}>{formatDate(f.created_at)}</span>
                </div>
                {f.feedback_text ? (
                  <div style={{ fontSize: '13.5px', color: '#334155', lineHeight: 1.5 }}>"{f.feedback_text}"</div>
                ) : (
                  <div style={{ fontSize: '13px', color: '#CBD5E1', fontStyle: 'italic' }}>No comment left</div>
                )}
                {f.ticket_id && (
                  <button
                    onClick={() => navigate(`/tickets/${f.ticket_id}`)}
                    style={{ marginTop: '8px', background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: '12px', fontWeight: '600', color: '#0E7490' }}
                  >
                    View conversation →
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Resolution analytics */}
      <section>
        <h2 style={{ margin: '0 0 12px', fontSize: '15px', fontWeight: '600', color: '#0F172A' }}>Resolution Analytics</h2>
        <p style={{ margin: '0 0 12px', fontSize: '12px', color: '#94A3B8' }}>Last {analytics?.window_days ?? 30} days</p>
        <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
          <StatCard label="Conversations Handled" value={analytics?.conversations_handled} loading={analyticsLoading} />
          <StatCard label="Resolved by Luna" value={analytics?.resolved_by_luna} loading={analyticsLoading} />
          <StatCard label="Escalated to Human" value={analytics?.escalated_to_human} loading={analyticsLoading} />
          {(analyticsLoading || analytics?.avg_response_time_seconds != null) && (
            <StatCard label="Avg Response Time" value={formatDuration(analytics?.avg_response_time_seconds)} loading={analyticsLoading} subtitle="Email only" />
          )}
          {(analyticsLoading || analytics?.approval_rate != null) && (
            <StatCard label="Approval Rate" value={analytics?.approval_rate != null ? `${analytics.approval_rate}%` : null} loading={analyticsLoading} />
          )}
          <StatCard label="Cancellations Requested" value={analytics?.cancellation_count} loading={analyticsLoading} />
          <StatCard label="Refunds Requested" value={analytics?.refund_count} loading={analyticsLoading} />
          {(analyticsLoading || analytics?.csat != null) && (
            <StatCard label="CSAT" value={analytics?.csat != null ? `${analytics.csat} ★` : null} loading={analyticsLoading} />
          )}
        </div>
      </section>

      {/* Autopilot readiness teaser — only when there's a real track record.
          Full detail (stats, why-not-ready explanation, activation control)
          lives on the dedicated Automation page now. */}
      {analytics?.autopilot_readiness && (
        <section>
          <div style={{ padding: '22px 24px', border: '1px solid #E4E4E7', borderRadius: '8px', background: '#F8FAFC' }}>
            <h3 style={{ margin: '0 0 6px', fontSize: '14px', fontWeight: '700', color: '#0F172A' }}>Luna is ready for Autopilot review</h3>
            <p style={{ margin: '0 0 14px', fontSize: '13px', color: '#475569', lineHeight: 1.55 }}>
              Luna has successfully handled {analytics.autopilot_readiness.eligible_cancellations} eligible cancellation
              request{analytics.autopilot_readiness.eligible_cancellations === 1 ? '' : 's'} with
              a {analytics.autopilot_readiness.approval_rate}% merchant approval rate. Autopilot would let Luna cancel
              eligible orders automatically. It stays off until you explicitly turn it on, and every action still
              requires your approval today.
            </p>
            <button
              onClick={() => navigate('/automation')}
              style={{ padding: '8px 16px', borderRadius: '6px', border: '1px solid #06B6D4', background: 'white', color: '#0E7490', fontSize: '13px', fontWeight: '600', cursor: 'pointer' }}
            >
              Review readiness →
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
