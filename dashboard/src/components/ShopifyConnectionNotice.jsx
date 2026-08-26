import { useState } from 'react';

// Text-based walkthrough, not screenshots — unlike the Gmail OAuth flow we
// don't have sanitized real captures of the Shopify install screens yet.
// Describing the real steps honestly (no invented UI, no mockups standing
// in for the real thing) is preferable to fabricating images of a screen we
// haven't captured. Swap this for real captures if/when they're taken.
const INSTALL_STEPS = [
  {
    title: '1. Click "Connect Shopify Store"',
    caption: 'You\'ll leave tResolv briefly and land on your store\'s Shopify admin, that\'s expected.',
  },
  {
    title: '2. Log in to your Shopify admin, if not already',
    caption: 'Use whichever account has access to install apps on this store.',
  },
  {
    title: '3. Review the permissions Shopify lists',
    caption: 'tResolv requests read access to orders and customers, so Luna can look up order status and process approved cancellations/exchanges.',
  },
  {
    title: '4. Click "Install app"',
    caption: 'Confirms the access described above, nothing more.',
  },
  {
    title: '5. Success',
    caption: 'Shopify redirects you back to tResolv with a "Connected" confirmation and your store name shown.',
  },
];

export default function ShopifyConnectionNotice() {
  const [showWalkthrough, setShowWalkthrough] = useState(false);

  return (
    <div style={{
      padding: '14px 16px', background: 'var(--info-bg, #F0F9FF)',
      border: '1px solid var(--info-border, #BAE6FD)', borderRadius: '6px',
      fontSize: '13px', lineHeight: '1.6', color: 'var(--text-secondary)',
    }}>
      <div style={{ fontWeight: '700', color: 'var(--text-primary)', marginBottom: '4px' }}>
        What happens when you connect
      </div>
      <p style={{ margin: '0 0 8px' }}>
        <strong>What we're asking for:</strong> read access to your store's orders and customers, so
        Luna can look up order status and act on cancellations/exchanges you've approved. We don't
        request access to billing, theme, or store settings.
      </p>

      <button
        type="button"
        onClick={() => setShowWalkthrough(v => !v)}
        style={{
          margin: '2px 0 8px', padding: '6px 12px', borderRadius: '6px',
          border: '1px solid var(--info-border, #BAE6FD)', background: 'white',
          color: 'var(--text-primary)', fontSize: '12px', fontWeight: '600', cursor: 'pointer',
        }}
      >
        {showWalkthrough ? 'Hide' : 'What will this look like? →'}
      </button>

      {showWalkthrough && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', margin: '4px 0 12px' }}>
          {INSTALL_STEPS.map(step => (
            <div key={step.title}>
              <div style={{ fontWeight: '600', color: 'var(--text-primary)', marginBottom: '2px', fontSize: '12px' }}>
                {step.title}
              </div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted, #94A3B8)' }}>
                {step.caption}
              </div>
            </div>
          ))}
        </div>
      )}

      <p style={{ margin: 0 }}>
        Questions before connecting? Email us at{' '}
        <a href="mailto:hello@tresolv.online" style={{ color: 'var(--accent)' }}>hello@tresolv.online</a>.
      </p>
    </div>
  );
}
