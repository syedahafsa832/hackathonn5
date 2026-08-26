import { useState } from 'react';

// Real screenshots of the actual Google OAuth screens tResolv sends a
// merchant through, captured from the live app (app.tresolv.online) — not
// mockups. Cropped to just the browser content (no tab strip, no taskbar)
// and each highlights the one thing to look for at that step. Files live in
// dashboard/public/gmail-onboarding/.
const OAUTH_STEPS = [
  {
    src: '/gmail-onboarding/01-settings-connect.png',
    title: '1. Click "Connect Gmail"',
    caption: 'Starts the Google sign-in. You\'ll leave tResolv briefly, that\'s expected.',
  },
  {
    src: '/gmail-onboarding/02-choose-account.png',
    title: '2. Pick the Google account for your support inbox',
    caption: 'Choose whichever Google account receives your customer emails.',
  },
  {
    src: '/gmail-onboarding/03-unverified-warning-advanced.png',
    title: '3. Click "Advanced"',
    caption: 'Google shows this because tResolv is still mid-verification (see above). Click Advanced to see the option to continue.',
  },
  {
    src: '/gmail-onboarding/04-unverified-warning-continue.png',
    title: '4. Click "Go to tResolv (unsafe)"',
    caption: 'That\'s Google\'s standard wording for any unverified app, not a description of tResolv itself.',
  },
  {
    src: '/gmail-onboarding/05-consent-screen.png',
    title: '5. Review the permissions, then click "Continue"',
    caption: 'Confirms the exact access described above, nothing more.',
  },
  {
    src: '/gmail-onboarding/06-connected-success.png',
    title: '6. Success',
    caption: 'You\'re back in tResolv with a green "Gmail connected" confirmation and your email address shown.',
  },
];

// Google shows an "unverified app" warning on the OAuth consent screen for
// apps requesting sensitive Gmail scopes (gmail.modify) before they've
// completed Google's public verification review. This is genuinely true of
// tResolv right now — it is not a bug, not a security problem, and not
// something to hide. This notice explains it honestly instead of letting a
// merchant hit Google's raw warning with zero context. Shared between
// Onboarding's "Connect inbox" step and Settings' Email tab so the wording
// never drifts between the two places a merchant can trigger this OAuth flow.
export default function GmailUnverifiedNotice() {
  const [showWalkthrough, setShowWalkthrough] = useState(false);

  return (
    <div style={{
      padding: '14px 16px', background: 'var(--warning-bg, #FFFBEB)',
      border: '1px solid var(--warning-border, #FDE68A)', borderRadius: '6px',
      fontSize: '13px', lineHeight: '1.6', color: 'var(--text-secondary)',
    }}>
      <div style={{ fontWeight: '700', color: 'var(--text-primary)', marginBottom: '4px' }}>
        Heads up: Google may show an "unverified app" warning
      </div>
      <p style={{ margin: '0 0 8px' }}>
        tResolv is an early-stage product that hasn't yet completed Google's formal OAuth
        verification review for sending/reading email on your behalf. That review is a real,
        multi-step process for apps at our stage, and we're actively working through it. The
        warning doesn't mean anything is wrong with your account or unsafe about this
        connection.
      </p>
      <p style={{ margin: '0 0 8px' }}>
        <strong>What we're asking for:</strong> permission to read new messages in this inbox and
        send replies from it, so tResolv can spot customer support emails and respond. We don't
        request access to anything else in your Google account.
      </p>
      <p style={{ margin: '0 0 8px' }}>
        <strong>To continue:</strong> on Google's screen, click <em>"Advanced"</em>, then
        <em> "Go to tResolv (unsafe)"</em>. That wording is Google's standard label for any app
        mid-verification. It isn't tResolv's own description of itself.
      </p>

      <button
        type="button"
        onClick={() => setShowWalkthrough(v => !v)}
        style={{
          margin: '2px 0 8px', padding: '6px 12px', borderRadius: '6px',
          border: '1px solid var(--warning-border, #FDE68A)', background: 'white',
          color: 'var(--text-primary)', fontSize: '12px', fontWeight: '600', cursor: 'pointer',
        }}
      >
        {showWalkthrough ? 'Hide' : 'See what this looks like →'}
      </button>

      {showWalkthrough && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', margin: '4px 0 12px' }}>
          {OAUTH_STEPS.map(step => (
            <div key={step.src}>
              <div style={{ fontWeight: '600', color: 'var(--text-primary)', marginBottom: '4px', fontSize: '12px' }}>
                {step.title}
              </div>
              <img
                src={step.src}
                alt={step.title}
                loading="lazy"
                style={{ width: '100%', maxWidth: '520px', borderRadius: '6px', border: '1px solid var(--border, #E4E4E7)', display: 'block' }}
              />
              <div style={{ fontSize: '11px', color: 'var(--text-muted, #94A3B8)', marginTop: '4px' }}>
                {step.caption}
              </div>
            </div>
          ))}
        </div>
      )}

      <p style={{ margin: 0 }}>
        Concerned or want more detail before connecting? Email us at{' '}
        <a href="mailto:hello@tresolv.online" style={{ color: 'var(--accent)' }}>hello@tresolv.online</a>.
      </p>
    </div>
  );
}
