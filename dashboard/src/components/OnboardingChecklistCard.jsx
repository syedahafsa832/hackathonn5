import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Store, Database, Mail, Sparkles, PlayCircle, Rocket, CheckCircle2, Circle, ArrowRight, PartyPopper } from 'lucide-react';

const CELEBRATED_KEY = 'resolv_onboarding_celebrated';

const STEP_DEFS = [
  {
    key: 'shopify', title: 'Shopify', icon: Store, cta: 'Connect',
    doneCopy: 'Connected successfully',
    pendingCopy: 'Connect your store to sync products and orders',
  },
  {
    key: 'knowledge', title: 'Knowledge Base', icon: Database, cta: 'Import',
    doneCopy: 'Products, collections and policies imported',
    pendingCopy: 'Import products, collections and policies',
  },
  {
    key: 'gmail', title: 'Gmail', icon: Mail, cta: 'Connect',
    doneCopy: 'Inbox connected',
    pendingCopy: 'Connect the inbox Luna will monitor and reply from',
  },
  {
    key: 'replyStyle', title: 'Reply Style', icon: Sparkles, cta: 'Choose style',
    doneCopy: 'Personality configured',
    pendingCopy: "Choose Luna's personality",
  },
  {
    key: 'testAi', title: 'Test AI', icon: PlayCircle, cta: 'Start test',
    doneCopy: 'Test run or skipped',
    pendingCopy: 'Run your first AI conversation',
  },
  {
    // Distinct from the steps above: completing setup does not itself put
    // Luna live (see Onboarding.jsx StepGoLive) — this reflects the real
    // backend ai_mode, fetched by Dashboard.jsx, never inferred.
    key: 'goLive', title: 'Go Live', icon: Rocket, cta: 'Go Live',
    doneCopy: 'Luna is live and handling real conversations',
    pendingCopy: "Everything's ready. Activate Luna when you want it live",
  },
];

// Small, self-contained confetti burst — no external dependency for a
// one-time celebration moment. Auto-removes itself; never re-renders.
function ConfettiBurst() {
  const pieces = useMemo(() => {
    const colors = ['#06B6D4', '#22D3EE', '#10B981', '#F59E0B', '#8B5CF6'];
    return Array.from({ length: 36 }, (_, i) => ({
      id: i,
      left: Math.random() * 100,
      color: colors[i % colors.length],
      delay: Math.random() * 0.3,
      duration: 1.4 + Math.random() * 0.8,
      size: 6 + Math.random() * 5,
      rotate: Math.random() * 360,
    }));
  }, []);

  return (
    <div aria-hidden="true" style={{ position: 'absolute', inset: 0, overflow: 'hidden', pointerEvents: 'none', borderRadius: 'inherit' }}>
      {pieces.map(p => (
        <span
          key={p.id}
          style={{
            position: 'absolute', top: 0, left: `${p.left}%`,
            width: `${p.size}px`, height: `${p.size * 0.4}px`,
            background: p.color, borderRadius: '2px',
            transform: `rotate(${p.rotate}deg)`,
            animation: `confettiFall ${p.duration}s ease-in ${p.delay}s forwards`,
          }}
        />
      ))}
    </div>
  );
}

export default function OnboardingChecklistCard({ steps, onDismiss }) {
  const navigate = useNavigate();
  const items = STEP_DEFS.map(def => ({ ...def, done: !!steps[def.key] }));
  const doneCount = items.filter(i => i.done).length;
  const total = items.length;
  const percent = Math.round((doneCount / total) * 100);
  const allDone = doneCount === total;
  const currentIndex = items.findIndex(i => !i.done);

  const [celebrated, setCelebrated] = useState(() => localStorage.getItem(CELEBRATED_KEY) === 'true');
  const [showConfetti, setShowConfetti] = useState(false);
  const [ctaHover, setCtaHover] = useState(false);

  useEffect(() => {
    if (allDone && !celebrated) {
      setShowConfetti(true);
      localStorage.setItem(CELEBRATED_KEY, 'true');
      setCelebrated(true);
      const t = setTimeout(() => setShowConfetti(false), 2200);
      return () => clearTimeout(t);
    }
  }, [allDone, celebrated]);

  const goToOnboarding = () => navigate('/onboarding');
  const goToReplyStyle = () => navigate('/settings', { state: { tab: 'reply-style' } });

  return (
    <section
      aria-label="Onboarding progress"
      style={{
        position: 'relative', overflow: 'hidden',
        background: 'var(--bg-surface)', border: '1px solid var(--border-default)',
        borderRadius: '16px', padding: '24px',
        boxShadow: '0 1px 2px rgba(15,23,42,0.04), 0 8px 24px rgba(15,23,42,0.06)',
        display: 'flex', flexDirection: 'column', gap: '18px',
      }}
    >
      {showConfetti && <ConfettiBurst />}

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
          <div style={{
            width: '34px', height: '34px', borderRadius: '10px', flexShrink: 0,
            background: allDone ? 'var(--success-bg)' : 'var(--cyan-50)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            {allDone
              ? <PartyPopper size={18} color="var(--success)" />
              : <Sparkles size={18} color="var(--cyan-600)" />}
          </div>
          <div style={{ minWidth: 0 }}>
            <h3 style={{ margin: 0, fontSize: '16px', fontWeight: '700', color: 'var(--text-primary)' }}>
              {allDone ? "Luna is live" : 'Get Luna live'}
            </h3>
            <p style={{ margin: '2px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
              {allDone
                ? "Every step is complete and Luna is active. She's handling incoming tickets now."
                : 'Finish setup, then activate Luna so she can start resolving tickets automatically.'}
            </p>
          </div>
        </div>
        <button
          onClick={onDismiss}
          aria-label="Dismiss onboarding checklist"
          style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', cursor: 'pointer', fontSize: '12px', fontWeight: '500', padding: '4px', flexShrink: 0 }}
        >
          Dismiss
        </button>
      </div>

      {/* Progress bar */}
      <div>
        <div
          role="progressbar"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`Onboarding progress: ${percent}% complete`}
          style={{ height: '8px', borderRadius: '999px', background: 'var(--slate-100)', overflow: 'hidden' }}
        >
          <div style={{
            height: '100%', borderRadius: '999px', width: `${percent}%`,
            background: allDone
              ? 'linear-gradient(90deg, var(--success) 0%, #34D399 100%)'
              : 'linear-gradient(90deg, var(--cyan-500) 0%, var(--cyan-400) 100%)',
            transition: 'width 0.6s cubic-bezier(0.22, 1, 0.36, 1)',
          }} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '6px' }}>
          <span style={{ fontSize: '12px', fontWeight: '600', color: allDone ? 'var(--success)' : 'var(--cyan-600)' }}>
            {percent}% complete
          </span>
          <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
            {doneCount} of {total} steps completed
          </span>
        </div>
      </div>

      {/* Steps list */}
      <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {items.map((item, i) => {
          const status = item.done ? 'completed' : i === currentIndex ? 'current' : 'pending';
          const Icon = item.icon;
          return (
            <li
              key={item.key}
              style={{
                display: 'flex', alignItems: 'center', gap: '12px',
                padding: '10px 12px', borderRadius: '10px',
                background: status === 'current' ? 'var(--bg-subtle)' : 'transparent',
                border: status === 'current' ? '1px solid var(--cyan-200)' : '1px solid transparent',
                opacity: status === 'pending' ? 0.55 : 1,
                animation: 'rowFadeIn 0.35s ease both',
                animationDelay: `${i * 0.04}s`,
              }}
            >
              <span style={{
                width: '30px', height: '30px', borderRadius: '50%', flexShrink: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: status === 'completed' ? 'var(--success-bg)' : status === 'current' ? 'var(--cyan-50)' : 'var(--slate-100)',
                animation: status === 'completed' ? 'checkPop 0.35s ease-out' : 'none',
              }}>
                {status === 'completed'
                  ? <CheckCircle2 size={17} color="var(--success)" aria-hidden="true" />
                  : <Icon size={15} color={status === 'current' ? 'var(--cyan-600)' : 'var(--slate-400)'} aria-hidden="true" />}
              </span>

              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: '13.5px', fontWeight: '600', color: status === 'pending' ? 'var(--text-tertiary)' : 'var(--text-primary)' }}>
                  {item.title}
                </div>
                <div style={{ fontSize: '12.5px', color: 'var(--text-tertiary)', marginTop: '1px' }}>
                  {item.done ? item.doneCopy : item.pendingCopy}
                </div>
              </div>

              {status === 'completed' && (
                <span style={{ fontSize: '11px', fontWeight: '600', color: 'var(--success)', background: 'var(--success-bg)', padding: '3px 9px', borderRadius: '999px', flexShrink: 0 }}>
                  Done
                </span>
              )}

              {status === 'current' && (
                <button
                  onClick={item.key === 'replyStyle' ? goToReplyStyle : goToOnboarding}
                  style={{
                    fontSize: '12px', fontWeight: '600', color: 'white', background: 'var(--cyan-500)',
                    border: 'none', borderRadius: '7px', padding: '6px 12px', cursor: 'pointer',
                    flexShrink: 0, transition: 'background 0.15s',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.background = 'var(--cyan-600)'; }}
                  onMouseLeave={e => { e.currentTarget.style.background = 'var(--cyan-500)'; }}
                >
                  {item.cta}
                </button>
              )}

              {status === 'pending' && (
                <Circle size={14} color="var(--slate-400)" aria-hidden="true" style={{ flexShrink: 0 }} />
              )}
            </li>
          );
        })}
      </ul>

      {/* CTA */}
      {allDone ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13.5px', fontWeight: '600', color: 'var(--success)' }}>
          <PartyPopper size={16} aria-hidden="true" />
          Luna is ready
        </div>
      ) : (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px' }}>
          <button
            onClick={items[currentIndex]?.key === 'replyStyle' ? goToReplyStyle : goToOnboarding}
            onMouseEnter={() => setCtaHover(true)}
            onMouseLeave={() => setCtaHover(false)}
            style={{
              display: 'flex', alignItems: 'center', gap: '6px',
              fontSize: '13.5px', fontWeight: '700', color: 'white',
              background: 'var(--cyan-500)', border: 'none', borderRadius: '9px',
              padding: '9px 16px', cursor: 'pointer',
              boxShadow: ctaHover ? '0 4px 14px rgba(6,182,212,0.35)' : '0 2px 6px rgba(6,182,212,0.2)',
              transform: ctaHover ? 'translateY(-1px)' : 'none',
              transition: 'all 0.15s ease',
            }}
          >
            Finish setup
            <ArrowRight size={15} aria-hidden="true" />
          </button>
          <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
            Next: {items[currentIndex]?.title}
          </span>
        </div>
      )}
    </section>
  );
}
