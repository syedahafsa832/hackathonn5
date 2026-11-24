import { useCallback, useEffect, useState } from 'react';
import client, { extractErrorMessage } from '../api/client';

const buttonStyle = {
  padding: '7px 12px',
  border: '1px solid var(--border)',
  borderRadius: '4px',
  background: 'var(--bg-primary)',
  color: 'var(--text-primary)',
  fontSize: '12px',
  fontWeight: 600,
  cursor: 'pointer',
};

export default function EmailForwardingSetup({ brandId }) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await client.get('/api/brands/' + brandId + '/email-forwarding/status');
      setStatus(response.data);
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not load forwarding status.'));
    } finally {
      setLoading(false);
    }
  }, [brandId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const setup = async () => {
    setBusy(true);
    setMessage('');
    setError('');
    try {
      await client.post('/api/brands/' + brandId + '/email-forwarding/setup');
      await refresh();
      setMessage('Your private forwarding address is ready.');
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not create the forwarding address.'));
    } finally {
      setBusy(false);
    }
  };

  const connectSendOnly = async () => {
    setBusy(true);
    setMessage('');
    setError('');
    try {
      const response = await client.get('/api/brands/' + brandId + '/gmail/send-auth-url');
      window.location.href = response.data.auth_url;
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not start Gmail send-only authorization.'));
      setBusy(false);
    }
  };

  const confirmForwarding = async () => {
    setBusy(true);
    setMessage('');
    setError('');
    try {
      await client.post('/api/brands/' + brandId + '/email-forwarding/confirm');
      await refresh();
      setMessage('Forwarding marked as verified.');
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not confirm forwarding.'));
    } finally {
      setBusy(false);
    }
  };

  const copyAddress = async () => {
    try {
      await navigator.clipboard.writeText(status.forwarding_address);
      setMessage('Forwarding address copied.');
    } catch {
      setError('Clipboard access failed. Select and copy the address manually.');
    }
  };

  const codeReady = !!status?.verification_code;
  const hasAddress = !!status?.forwarding_address;

  return (
    <section style={{ marginTop: '16px', paddingTop: '16px', borderTop: '1px solid var(--border)' }}>
      <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        Free email forwarding
      </div>
      <p style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.55, margin: '0 0 12px' }}>
        Receive customer support emails through your private tResolv address. Your normal Gmail inbox stays in place. Gmail is connected separately for sending replies only.
      </p>

      {loading ? (
        <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Checking forwarding setup…</div>
      ) : (
        <>
          {hasAddress ? (
            <div style={{ padding: '10px 12px', border: '1px solid var(--border)', borderRadius: '5px', marginBottom: '12px' }}>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '5px' }}>Your forwarding address</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                <code style={{ fontSize: '12px', overflowWrap: 'anywhere', color: 'var(--text-primary)' }}>{status.forwarding_address}</code>
                <button type="button" onClick={copyAddress} style={buttonStyle}>Copy</button>
              </div>
              <div style={{ display: 'flex', gap: '7px', alignItems: 'center', marginTop: '9px', fontSize: '12px' }}>
                <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: status.verified ? 'var(--success)' : 'var(--warning, #d97706)' }} />
                <span style={{ color: status.verified ? 'var(--success)' : 'var(--text-secondary)' }}>
                  {status.verified ? 'Forwarding verified' : 'Waiting for Gmail verification'}
                </span>
              </div>
            </div>
          ) : (
            <button type="button" disabled={busy} onClick={setup} style={buttonStyle}>
              {busy ? 'Creating…' : 'Create my forwarding address'}
            </button>
          )}

          {hasAddress && !status.verified && (
            <div style={{ padding: '10px 12px', background: 'var(--bg-secondary)', borderRadius: '5px', marginBottom: '12px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '6px' }}>Verify the address in Gmail</div>
              <ol style={{ paddingLeft: '18px', margin: 0, fontSize: '12px', lineHeight: 1.65, color: 'var(--text-secondary)' }}>
                <li>Open <a href="https://mail.google.com/mail/u/0/#settings/fwdandpop" target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>Gmail Forwarding settings</a> for your support inbox.</li>
                <li>Add the tResolv address above as a forwarding address.</li>
                <li>Return here and refresh after Google sends its confirmation email.</li>
                <li>Copy the code below, enter it in Gmail, and confirm the address.</li>
              </ol>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '10px' }}>
                <button type="button" disabled={busy} onClick={refresh} style={buttonStyle}>Refresh verification code</button>
                {codeReady && (
                  <div style={{ fontSize: '16px', fontWeight: 700, letterSpacing: '0.16em', fontFamily: 'monospace', padding: '6px 10px', border: '1px dashed var(--border)', borderRadius: '4px' }}>
                    {status.verification_code}
                  </div>
                )}
              </div>
              {codeReady && (
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '6px' }}>
                  Code expires from this screen after 15 minutes. Request a new confirmation email if it expires.
                </div>
              )}
              <button type="button" disabled={busy} onClick={confirmForwarding} style={{ ...buttonStyle, marginTop: '10px' }}>
                I entered the code in Gmail
              </button>
            </div>
          )}

          {hasAddress && (
            <div style={{ marginTop: '12px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', marginBottom: '6px' }}>
                <span style={{ fontSize: '12px', fontWeight: 600 }}>Gmail send access</span>
                <span style={{ fontSize: '11px', color: status.gmail_send_connected ? 'var(--success)' : 'var(--text-muted)' }}>
                  {status.gmail_send_connected ? ('Connected: ' + (status.gmail_email || '')) : 'Not connected'}
                </span>
              </div>
              {!status.gmail_send_connected && (
                <button type="button" disabled={busy} onClick={connectSendOnly} style={buttonStyle}>
                  {busy ? 'Connecting…' : 'Connect Gmail for sending only'}
                </button>
              )}
              <p style={{ fontSize: '11px', lineHeight: 1.5, color: 'var(--text-muted)', margin: '8px 0 0' }}>
                This uses a separate Gmail permission request for sending replies. It does not read your inbox.
              </p>
            </div>
          )}
        </>
      )}

      {message && <div role="status" style={{ fontSize: '12px', color: 'var(--success)', marginTop: '9px' }}>{message}</div>}
      {error && <div role="alert" style={{ fontSize: '12px', color: 'var(--danger)', marginTop: '9px' }}>{error}</div>}
    </section>
  );
}
