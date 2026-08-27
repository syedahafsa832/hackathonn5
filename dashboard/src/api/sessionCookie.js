// Presence-only "is this browser logged into the app" signal for the
// marketing site (resolv-site, a separate origin under the same
// .tresolv.online parent domain) to swap its nav CTA between "Sign up" and
// "Dashboard" — see resolv-site/lib/auth.js, which already reads exactly
// this cookie. Never holds the access/refresh token or any user data: the
// value is always the literal string "1", nothing else.
//
// Written with document.cookie (not a Set-Cookie response header) so it
// works from a pure SPA with no backend round trip dedicated to it — the
// login/refresh responses already return `expires_in`, which is all this
// needs. Domain=.tresolv.online only succeeds when actually served from
// *.tresolv.online (browsers reject a Domain that isn't the current host's
// own registrable domain or a parent of it) - a silent no-op everywhere
// else (e.g. local dev on localhost), which is the correct behavior, not a
// bug to guard around.
const COOKIE_NAME = 'tresolv_logged_in';
const COOKIE_ATTRS = 'Domain=.tresolv.online; Path=/; Secure; SameSite=Lax';

// Supabase's default access-token lifetime — used only if a login/refresh
// response is ever missing expires_in, so the cookie still gets a sane,
// conservative (short, never overstated) lifetime instead of persisting
// indefinitely.
const DEFAULT_MAX_AGE_SECONDS = 3600;

export function setLoggedInCookie(expiresInSeconds) {
  if (typeof document === 'undefined') return;
  const maxAge = Number.isFinite(expiresInSeconds) && expiresInSeconds > 0
    ? Math.floor(expiresInSeconds)
    : DEFAULT_MAX_AGE_SECONDS;
  document.cookie = `${COOKIE_NAME}=1; Max-Age=${maxAge}; ${COOKIE_ATTRS}`;
}

// Must be called on logout AND whenever a refresh token turns out to be
// invalid/expired (client.js) — the cookie must never outlive the real
// session it represents.
export function clearLoggedInCookie() {
  if (typeof document === 'undefined') return;
  document.cookie = `${COOKIE_NAME}=; Max-Age=0; ${COOKIE_ATTRS}`;
}
