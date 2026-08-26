import { LifeBuoy } from 'lucide-react';

// The one existing, safe way a user can reach tResolv: a plain mailto link.
// This is not a new support platform — hello@tresolv.online already exists
// as the contact channel used inside GmailUnverifiedNotice; this component
// just makes it consistently reachable everywhere instead of living in one
// component. No new backend, no form, no data collection, nothing that can
// leak logs/tokens/customer data — the only payload is a short subject line
// built from `context` (a page name or short error label, e.g. "Settings"
// or "App render error"), never an actual error message, stack trace, or
// any user data.
const SUPPORT_EMAIL = 'hello@tresolv.online';

function buildMailto(context) {
  const subject = context ? `tResolv help: ${context}` : 'tResolv help';
  return `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(subject)}`;
}

/**
 * variant="icon" (default): small icon+label button, for a persistent header/toolbar.
 * variant="inline": plain underlined text link, for dropping into existing
 *   copy/error states ("...if this keeps happening, <Contact support/>.").
 */
export default function HelpContactLink({ context, variant = 'icon', label, style }) {
  const href = buildMailto(context);

  if (variant === 'inline') {
    return (
      <a href={href} style={{ color: 'var(--accent, #06B6D4)', fontWeight: 600, textDecoration: 'underline', ...style }}>
        {label || 'contact support'}
      </a>
    );
  }

  return (
    <a
      href={href}
      aria-label="Contact tResolv support"
      title={`Need help? Email ${SUPPORT_EMAIL}`}
      style={{
        display: 'flex', alignItems: 'center', gap: '6px', padding: '6px 10px',
        borderRadius: '4px', border: '1px solid var(--border)', background: 'transparent',
        color: 'var(--text-secondary)', fontSize: '13px', fontWeight: '500',
        textDecoration: 'none', cursor: 'pointer', flexShrink: 0,
        ...style,
      }}
    >
      <LifeBuoy size={14} />
      <span className="topbar-btn-label">{label || 'Help'}</span>
    </a>
  );
}
