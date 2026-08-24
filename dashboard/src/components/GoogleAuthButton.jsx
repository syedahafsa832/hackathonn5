import { useEffect, useRef, useState } from 'react';
import client, { extractErrorMessage } from '../api/client';

const CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID;

/**
 * Renders Google's own "Sign in with Google" button and exchanges the
 * resulting ID token for a Resolv session via POST /api/v1/auth/google.
 */
export default function GoogleAuthButton({ onSuccess, onError, text = 'continue_with' }) {
  const buttonRef = useRef(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!CLIENT_ID) return;

    let cancelled = false;

    const init = () => {
      if (cancelled || !window.google?.accounts?.id) return;

      window.google.accounts.id.initialize({
        client_id: CLIENT_ID,
        callback: async ({ credential }) => {
          try {
            const res = await client.post('/api/v1/auth/google', { credential });
            onSuccess?.(res.data);
          } catch (err) {
            onError?.(extractErrorMessage(err, 'Google sign-in failed. Please try again.'));
          }
        },
      });

      if (buttonRef.current) {
        window.google.accounts.id.renderButton(buttonRef.current, {
          type: 'standard',
          theme: 'outline',
          size: 'large',
          width: 372,
          text,
        });
      }
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
  }, [text, onSuccess, onError]);

  if (!CLIENT_ID) return null;

  return (
    <div>
      <div ref={buttonRef} />
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
