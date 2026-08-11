// Google shows an "unverified app" warning on the OAuth consent screen for
// apps requesting sensitive Gmail scopes (gmail.modify) before they've
// completed Google's public verification review. This is genuinely true of
// tResolv right now — it is not a bug, not a security problem, and not
// something to hide. This notice explains it honestly instead of letting a
// merchant hit Google's raw warning with zero context. Shared between
// Onboarding's "Connect inbox" step and Settings' Email tab so the wording
// never drifts between the two places a merchant can trigger this OAuth flow.
export default function GmailUnverifiedNotice() {
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
        mid-verification — it isn't tResolv's own description of itself.
      </p>
      <p style={{ margin: 0 }}>
        Concerned or want more detail before connecting? Email us at{' '}
        <a href="mailto:hello@tresolv.online" style={{ color: 'var(--accent)' }}>hello@tresolv.online</a>.
      </p>
    </div>
  );
}
