const API_BASE = import.meta.env.VITE_API_URL || import.meta.env.VITE_API_BASE_URL;

/**
 * "Continue with Google" button for tResolv ACCOUNT sign-in/signup only —
 * never confuse with the separate, later Gmail-inbox-connection OAuth
 * (brand_gmail_service.py) that happens inside the app.
 *
 * Navigates the whole browser to the backend's redirect-based OAuth flow
 * (GET /api/v1/auth/google/start) rather than using Google Identity
 * Services' client-side google.accounts.id.initialize/renderButton, which
 * required the exact page origin to be pre-registered in Google Cloud
 * Console's "Authorized JavaScript origins" — the origin-dependence that
 * broke sign-in from an origin not on that list (Google's own "400:
 * malformed request"). The redirect flow only needs the backend's own
 * fixed callback URL registered with Google, once, ever — it works from
 * any origin this backend already trusts (see cors.py's
 * _get_allowed_origins, reused server-side as the return-address check).
 * The resulting session comes back via GoogleAuthCallback.jsx.
 */
export default function GoogleAuthButton({ text = 'continue_with' }) {
  if (!API_BASE) return null;

  const label = text === 'signup_with' ? 'Sign up with Google' : 'Continue with Google';
  const href = `${API_BASE}/api/v1/auth/google/start?return_to=${encodeURIComponent(window.location.origin)}`;

  return (
    <a
      href={href}
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '10px',
        height: '40px', borderRadius: '6px', border: '1px solid var(--border-strong)',
        fontSize: '14px', fontWeight: 500, color: 'var(--text-primary)', textDecoration: 'none',
        width: '100%', boxSizing: 'border-box',
      }}
    >
      <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
        <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.9c1.7-1.57 2.68-3.87 2.68-6.62z" />
        <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.9-2.26c-.8.54-1.83.86-3.06.86-2.35 0-4.34-1.59-5.05-3.72H.98v2.33A9 9 0 0 0 9 18z" />
        <path fill="#FBBC05" d="M3.95 10.7A5.4 5.4 0 0 1 3.68 9c0-.59.1-1.17.27-1.7V4.97H.98A9 9 0 0 0 0 9c0 1.45.35 2.83.98 4.03z" />
        <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .98 4.97L3.95 7.3C4.66 5.17 6.65 3.58 9 3.58z" />
      </svg>
      {label}
    </a>
  );
}
