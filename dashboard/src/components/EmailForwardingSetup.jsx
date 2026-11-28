import { useCallback, useEffect, useRef, useState } from 'react';
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
  const [checking, setChecking] = useState(false);
  const [copied, setCopied] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [emailDataConsent, setEmailDataConsent] = useState(false);
  const hasLoadedRef = useRef(false);

  // Refresh in place after the first load. The old implementation replaced
  // the entire setup card with a loading message on every click, making the
  // confirmation button look unresponsive and causing the layout to jump.
  const refresh = useCallback(async ({ manual = false } = {}) => {
    if (!hasLoadedRef.current) setLoading(true);
    if (manual) {
      setChecking(true);
      setMessage('');
      setError('');
    }

    try {
      const response = await client.get('/api/brands/' + brandId + '/email-forwarding/status');
      setStatus(response.data);
      return response.data;
    } catch (err) {
      // Automatic background checks should not flash errors. Show an error
      // for the initial load and for an explicit user-requested check.
      if (!hasLoadedRef.current || manual) {
        setError(extractErrorMessage(err, 'Could not load forwarding status.'));
      }
      return null;
    } finally {
      hasLoadedRef.current = true;
      setLoading(false);
      if (manual) setChecking(false);
    }
  }, [brandId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const codeReady = !!status?.verification_code;
  const linkReady = !!status?.verification_link;
  const hasAddress = !!status?.forwarding_address;

  const ensureEmailDataConsent = async () => {
    if (status?.ai_processing_consent_recorded) return true;
    if (!emailDataConsent) {
      throw new Error('Please review and accept the email processing notice before continuing.');
    }
    await client.post('/api/brands/' + brandId + '/email-forwarding/consent');
    const updated = await refresh();
    if (!updated?.ai_processing_consent_recorded) {
      throw new Error('Consent could not be confirmed. Refresh and try again.');
    }
    setEmailDataConsent(false);
    return true;
  };

  // Keep checking for Google's confirmation in the background while the
  // setup card is open. Merchants no longer need to guess whether the
  // "Check" button worked or repeatedly click it.
  useEffect(() => {
    if (!hasAddress || status?.verified || codeReady || linkReady) return undefined;

    const intervalId = window.setInterval(() => {
      refresh();
    }, 12000);

    return () => window.clearInterval(intervalId);
  }, [hasAddress, status?.verified, codeReady, linkReady, refresh]);

  const setup = async () => {
    setBusy(true);
    setMessage('');
    setError('');
    try {
      await client.post('/api/brands/' + brandId + '/email-forwarding/setup');
      const updated = await refresh();
      if (updated) setMessage('Your private forwarding address is ready.');
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
      await ensureEmailDataConsent();
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
    setBusy(true);
    setMessage('');
    setError('');
    try {
      await ensureEmailDataConsent();
      await client.post('/api/brands/' + brandId + '/email-forwarding/activate');
      const updated = await refresh();
      setMessage(updated ? 'Email forwarding is now active.' : 'Forwarding was activated. Refresh the status to confirm.');
      if (onComplete) onComplete();
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not activate forwarding.'));
    } finally {
      setBusy(false);
    }
  };

  const pauseForwarding = async () => {
    setBusy(true);
    setMessage('');
    setError('');
    try {
      await client.post('/api/brands/' + brandId + '/email-forwarding/pause');
      const updated = await refresh();
      if (updated) setMessage('Gmail polling has been restored.');
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not pause forwarding.'));
    } finally {
      setBusy(false);
    }
  };

  const confirmForwarding = async () => {
    setBusy(true);
    setMessage('');
    setError('');
    try {
      await client.post('/api/brands/' + brandId + '/email-forwarding/confirm');
      const updated = await refresh();
      if (updated?.verified) {
        setMessage('Google confirmed your forwarding address. One step left: connect Gmail to send replies.');
      } else {
        setMessage('Confirmation submitted. Refreshing your status…');
      }
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not confirm forwarding.'));
    } finally {
      setBusy(false);
    }
  };

  const copyAddress = async () => {
    try {
      await navigator.clipboard.writeText(status.forwarding_address);
      setCopied(true);
      setMessage('Forwarding address copied.');
      setError('');
    } catch {
      setError('Clipboard access failed. Select and copy the address manually.');
    }
  };

  return (
    <section style={{ marginTop: '16px', paddingTop: '16px', borderTop: '1px solid var(--border)' }} aria-busy={loading || busy || checking}>
      <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        Receive customer support emails
      </div>
      <p style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.55, margin: '0 0 12px' }}>
        tResolv receives support emails through a private forwarding address. Your Gmail inbox stays in Gmail. The Google connection below is used only to send replies; it does not read your mailbox.
      </p>

      {loading ? (
        <div role="status" style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Checking forwarding setup…</div>
      ) : (
        <>
          {hasAddress ? (
            <div style={{ padding: '10px 12px', border: '1px solid var(--border)', borderRadius: '5px', marginBottom: '12px' }}>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '5px' }}>Your forwarding address</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                <code style={{ fontSize: '12px', overflowWrap: 'anywhere', color: 'var(--text-primary)' }}>{status.forwarding_address}</code>
                <button type="button" onClick={copyAddress} style={buttonStyle}>
                  {copied ? 'Copied ✓' : 'Copy address'}
                </button>
              </div>
              <div style={{ display: 'flex', gap: '7px', alignItems: 'center', marginTop: '9px', fontSize: '12px' }}>
                <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: status.verified ? 'var(--success)' : 'var(--warning, #d97706)' }} />
                <span style={{ color: status.verified ? 'var(--success)' : 'var(--text-secondary)' }}>
                  {status.verified ? 'Forwarding verified' : 'Waiting for Google confirmation'}
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
              <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '6px' }}>Connect your Gmail inbox</div>
              <ol style={{ paddingLeft: '18px', margin: 0, fontSize: '12px', lineHeight: 1.65, color: 'var(--text-secondary)' }}>
                <li>Open <a href="https://mail.google.com/mail/u/0/#settings/fwdandpop" target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>Gmail Forwarding settings</a> for the inbox that receives customer support messages.</li>
                <li>Choose <strong>Add a forwarding address</strong>, paste the exact tResolv address above, then click <strong>Next → Proceed → OK</strong>.</li>
                <li>Keep this tResolv page open. Google sends its confirmation to tResolv's private address, not to your normal inbox. We'll check for it automatically.</li>
                <li>When a code or link appears below, use it to finish Google's confirmation. Then return to Gmail, select the tResolv address in the forwarding dropdown, choose <strong>Keep Gmail's copy in the Inbox</strong>, and save.</li>
                <li>Come back here to connect Gmail for sending replies and activate forwarding.</li>
              </ol>

              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginTop: '10px' }}>
                <button
                  type="button"
                  disabled={busy || checking}
                  onClick={() => refresh({ manual: true })}
                  style={{ ...buttonStyle, opacity: checking ? 0.7 : 1 }}
                >
                  {checking ? 'Checking…' : 'Check now'}
                </button>
                <span role="status" style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                  {checking ? 'Looking for Google’s confirmation…' : 'We check automatically while this page is open.'}
                </span>
              </div>

              {codeReady && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '10px' }}>
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Google confirmation code</span>
                  <code style={{ fontSize: '16px', fontWeight: 700, letterSpacing: '0.16em', fontFamily: 'monospace', padding: '6px 10px', border: '1px dashed var(--border)', borderRadius: '4px', width: 'fit-content' }}>
                    {status.verification_code}
                  </code>
                </div>
              )}

              {linkReady && (
                <div style={{ marginTop: '8px', padding: '9px 10px', border: '1px solid var(--border)', borderRadius: '4px' }}>
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '5px' }}>Google confirmation link</div>
                  <a
                    href={status.verification_link}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: 'var(--accent)', fontSize: '12px', fontWeight: 600, overflowWrap: 'anywhere' }}
                  >
                    Open Google confirmation page →
                  </a>
                  <p style={{ fontSize: '11px', color: 'var(--text-muted)', margin: '5px 0 0' }}>
                    Open this link, finish Google's confirmation page, then return here.
                  </p>
                </div>
              )}

              {(codeReady || linkReady) && (
                <button type="button" disabled={busy} onClick={confirmForwarding} style={{ ...buttonStyle, marginTop: '10px' }}>
                  {busy ? 'Confirming…' : "I've completed Google's confirmation"}
                </button>
              )}

              {!codeReady && !linkReady && (
                <p style={{ fontSize: '11px', color: 'var(--text-muted)', margin: '8px 0 0' }}>
                  No code or link yet? Leave this page open. We'll keep checking. You can keep working in another tab while tResolv waits for Google's message.
                </p>
              )}
            </div>
          )}

          {hasAddress && (
            <div style={{ marginTop: '12px', padding: '11px 12px', border: '1px solid var(--border)', borderRadius: '5px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '6px' }}>
                How tResolv processes forwarded customer emails
              </div>
              {status.ai_processing_consent_recorded ? (
                <p style={{ fontSize: '11px', lineHeight: 1.55, color: 'var(--success)', margin: 0 }}>
                  Email processing consent recorded. You can review the current details in the <a href="https://www.tresolv.online/privacy" target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>Privacy Policy</a>.
                </p>
              ) : (
                <>
                  <p style={{ fontSize: '11px', lineHeight: 1.55, color: 'var(--text-secondary)', margin: '0 0 9px' }}>
                    When you activate email support, you instruct tResolv to receive the customer messages you forward, store them as support tickets, and process their contents to classify requests, retrieve relevant store information, and prepare replies. For forwarded email content, tResolv uses Cloudflare Workers AI, Mistral AI, and Groq. This email content is not routed to DeepSeek or OpenRouter and is not used by tResolv to train generalized AI models. These providers may retain limited request data under their own security and service policies. Read the <a href="https://www.tresolv.online/privacy" target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>Privacy Policy</a>.
                  </p>
                  <label style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', fontSize: '11px', lineHeight: 1.55, color: 'var(--text-primary)', cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={emailDataConsent}
                      onChange={(event) => setEmailDataConsent(event.target.checked)}
                      style={{ marginTop: '2px' }}
                    />
                    <span>I am authorised to connect this store and instruct tResolv to receive, store, and process the customer emails I forward for the email-support features described above.</span>
                  </label>
                </>
              )}
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
