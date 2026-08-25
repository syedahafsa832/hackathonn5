import { useEffect, useRef, useState } from 'react';
import client, { extractErrorMessage } from '../api/client';

const CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID;
const MAX_GOOGLE_BUTTON_WIDTH = 400; // Google's renderButton caps out around here regardless of what's passed.

/**
 * Renders Google's own "Sign in with Google" button and exchanges the
 * resulting ID token for a Resolv session via POST /api/v1/auth/google.
 */
export default function GoogleAuthButton({ onSuccess, onError, text = 'continue_with' }) {
  const containerRef = useRef(null);
  const buttonRef = useRef(null);
  const [ready, setReady] = useState(false);

  // Callbacks are handed a fresh closure on every parent render (they're
  // inline arrow functions in Login.jsx/Signup.jsx) — capturing them in a
  // ref instead of a useEffect dependency keeps the init effect below from
  // re-running (and re-calling google.accounts.id.initialize()) on every
  // keystroke in the surrounding form.
  const onSuccessRef = useRef(onSuccess);
  const onErrorRef = useRef(onError);
  useEffect(() => {
    onSuccessRef.current = onSuccess;
    onErrorRef.current = onError;
  }, [onSuccess, onError]);

  useEffect(() => {
    if (!CLIENT_ID) return;

    let cancelled = false;

    const init = () => {
      if (cancelled || !window.google?.accounts?.id || !containerRef.current) return;

      window.google.accounts.id.initialize({
        client_id: CLIENT_ID,
        callback: async ({ credential }) => {
          try {
            const res = await client.post('/api/v1/auth/google', { credential });
            onSuccessRef.current?.(res.data);
          } catch (err) {
            onErrorRef.current?.(extractErrorMessage(err, 'Google sign-in failed. Please try again.'));
          }
        },
      });

      // Match the width of the surrounding form (email/password inputs are
      // width:100%) instead of a fixed pixel value that doesn't track the
      // card's actual padding/max-width across Login vs Signup.
      const width = Math.min(containerRef.current.offsetWidth, MAX_GOOGLE_BUTTON_WIDTH);

      window.google.accounts.id.renderButton(containerRef.current, {
        type: 'standard',
        theme: 'outline',
        size: 'large',
        width,
        text,
      });
      setReady(true);
    };

    // The GIS script is loaded async in index.html — poll briefly until it's available.
    if (window.google?.accounts?.id) {
      init();
    } else {
      const interval = setInterval(() => {
        if (window.google?.accounts?.id) {
          clearInterval(interval);
          init();
        }
      }, 100);
      return () => { cancelled = true; clearInterval(interval); };
    }
    // Intentionally excludes onSuccess/onError/text — see the refs above.
    // Re-running this only on mount (and CLIENT_ID, which never changes at
    // runtime) is what stops the repeated-initialize() warning.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!CLIENT_ID) return null;

  return (
    <div ref={buttonRef} style={{ width: '100%' }}>
      <div ref={containerRef} />
      {!ready && (
        <div style={{
          height: '40px',
          borderRadius: '6px',
          border: '1px solid var(--border-strong)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '13px',
          color: 'var(--text-muted)',
        }}>
          Loading Google Sign-In…
        </div>
      )}
    </div>
  );
}
