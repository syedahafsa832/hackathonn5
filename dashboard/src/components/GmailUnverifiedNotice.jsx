// Explain Google's unverified-app warning accurately for the send-only OAuth flow.
export default function GmailUnverifiedNotice() {
  return (
    <div style={{
      padding: '12px 14px',
      background: 'var(--warning-bg, #FFFBEB)',
      border: '1px solid var(--warning-border, #FDE68A)',
      borderRadius: '6px',
      fontSize: '12px',
      lineHeight: '1.6',
      color: 'var(--text-secondary)',
    }}>
      <div style={{ fontWeight: 700, color: 'var(--text-primary)', marginBottom: '4px' }}>
        Google may show an “app isn’t verified” warning
      </div>
      <p style={{ margin: '0 0 6px' }}>
        tResolv's Gmail send-only OAuth request is still going through Google's verification process.
        If Google shows this warning, review the app name and requested access before deciding whether to continue.
      </p>
      <p style={{ margin: 0 }}>
        <strong>Requested Gmail access:</strong> send email only (<code>gmail.send</code>).
        tResolv receives incoming support messages through your forwarding address; this Google connection
        does not grant tResolv permission to read, modify, or delete your Gmail inbox.
      </p>
    </div>
  );
}
