import { useCallback, useEffect, useState } from 'react';
import client, { extractErrorMessage } from '../api/client';
import GmailUnverifiedNotice from './GmailUnverifiedNotice';

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

export default function EmailForwardingSetup({ brandId, returnTo = 'brands', onComplete }) {
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
      const response = await client.get('/api/brands/' + brandId + '/gmail/send-auth-url', {
        params: { return_to: returnTo },
      });
      window.location.href = response.data.auth_url;
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not start Gmail send-only authorization.'));
      setBusy(false);
    }
  };

  const activateForwarding = async () => {
    setBusy(true); setMessage(''); setError('');
    try {
      await client.post('/api/brands/' + brandId + '/email-forwarding/activate');
      await refresh();
      setMessage('Email forwarding is now active.');
      if (onComplete) onComplete();
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not activate forwarding.'));
    } finally { setBusy(false); }
  };

  const pauseForwarding = async () => {
    setBusy(true); setMessage(''); setError('');
    try {
      await client.post('/api/brands/' + brandId + '/email-forwarding/pause');
      await refresh();
      setMessage('Gmail polling has been restored.');
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not pause forwarding.'));
    } finally { setBusy(false); }
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
        Receive customer support emails
      </div>
      <p style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.55, margin: '0 0 12px' }}>
        tResolv receives support emails through a private forwarding address. Your Gmail inbox stays in Gmail. The Google connection below is used only to send replies; it does not read your mailbox.
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
                <li>Refresh this page after Gmail sends its confirmation code. Enter that code in Gmail, then click the confirmation button below.</li>
                <li>In Gmail, select <strong>Forward a copy of incoming mail</strong> to the tResolv address, choose <strong>Keep Gmail's copy in the Inbox</strong>, and save changes.</li>
                <li>Return here and activate forwarding after the address is verified and Gmail sending is connected.</li>
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
                  The code stays here until you confirm it or request a new one.
                </div>
              )}
              <button type="button" disabled={busy || !codeReady} onClick={confirmForwarding} style={{ ...buttonStyle, marginTop: '10px', opacity: (busy || !codeReady) ? 0.55 : 1 }}>
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
                <>
                  <GmailUnverifiedNotice />
                  <button type="button" disabled={busy || !status.verified} onClick={connectSendOnly} style={{ ...buttonStyle, opacity: (busy || !status.verified) ? 0.55 : 1, marginTop: '8px' }}>
                    {busy ? 'Connecting…' : 'Connect Gmail to send replies'}
                  </button>
                  {!status.verified && (
                    <p style={{ fontSize: '11px', lineHeight: 1.5, color: 'var(--text-muted)', margin: '8px 0 0' }}>
                      Verify the forwarding address first. The send-only Google connection unlocks after that.
                    </p>
                  )}
                </>
              )}
              <p style={{ fontSize: '11px', lineHeight: 1.5, color: 'var(--text-muted)', margin: '8px 0 0' }}>
                tResolv requests permission to send messages only. Incoming support emails are delivered through the forwarding address, not Gmail inbox access.
              </p>
              {status.verified && status.gmail_send_connected && status.ingestion_mode !== 'forwarding' && (
                <button type="button" disabled={busy} onClick={activateForwarding} style={{ ...buttonStyle, marginTop: '10px' }}>
                  {busy ? 'Activating…' : "I've enabled forwarding in Gmail — activate"}
                </button>
              )}
              {status.ingestion_mode === 'forwarding' && (
                <div style={{ marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div role="status" style={{ fontSize: '12px', color: 'var(--success)' }}>
                    Email forwarding is active. New support emails are routed to tResolv, and Gmail API access is send-only.
                  </div>
                  {onComplete && (
                    <button type="button" onClick={onComplete} style={buttonStyle}>
                      Continue →
                    </button>
                  )}
                  {status.legacy_gmail_connected && (
                    <button type="button" disabled={busy} onClick={pauseForwarding} style={{ ...buttonStyle, marginTop: '2px' }}>
                      {busy ? 'Switching…' : 'Pause forwarding and use the existing Gmail connection'}
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {message && <div role="status" style={{ fontSize: '12px', color: 'var(--success)', marginTop: '9px' }}>{message}</div>}
      {error && <div role="alert" style={{ fontSize: '12px', color: 'var(--danger)', marginTop: '9px' }}>{error}</div>}
    </section>
  );
}
