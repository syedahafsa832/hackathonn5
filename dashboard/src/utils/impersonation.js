// Founder/admin "view as tenant" session swap — see backend
// platform_admin.py's POST /admin/tenants/{id}/impersonate. Uses
// sessionStorage (not localStorage) for the stashed admin session and the
// "currently impersonating" marker, so neither survives a browser restart
// or leaks into a different tab on a shared machine.

const ADMIN_TOKEN_KEY = 'resolv_admin_token';
const ADMIN_REFRESH_KEY = 'resolv_admin_refresh_token';
const IMPERSONATING_KEY = 'resolv_impersonating';

export function isImpersonating() {
  return !!sessionStorage.getItem(IMPERSONATING_KEY) && !!sessionStorage.getItem(ADMIN_TOKEN_KEY);
}

export function getImpersonatedTenant() {
  try {
    const raw = sessionStorage.getItem(IMPERSONATING_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

// Swaps the active session to the impersonation token the backend just
// minted, stashing the admin's own session first so it can be restored.
export function startImpersonation(tenant, accessToken) {
  if (!sessionStorage.getItem(ADMIN_TOKEN_KEY)) {
    const ownToken = localStorage.getItem('resolv_token');
    const ownRefresh = localStorage.getItem('resolv_refresh_token');
    if (ownToken) sessionStorage.setItem(ADMIN_TOKEN_KEY, ownToken);
    if (ownRefresh) sessionStorage.setItem(ADMIN_REFRESH_KEY, ownRefresh);
  }
  sessionStorage.setItem(IMPERSONATING_KEY, JSON.stringify(tenant));
  localStorage.setItem('resolv_token', accessToken);
  // No refresh token for an impersonation session — it must expire on its
  // own rather than silently renewing itself past the backend's short TTL.
  localStorage.removeItem('resolv_refresh_token');
  window.location.href = '/dashboard';
}

// Restores the admin's own session. Safe to call even with nothing stashed
// (e.g. the impersonation token already expired and the request
// interceptor cleared the local session) — just drops the stale markers.
export function exitImpersonation() {
  const ownToken = sessionStorage.getItem(ADMIN_TOKEN_KEY);
  const ownRefresh = sessionStorage.getItem(ADMIN_REFRESH_KEY);
  clearImpersonationMarkers();
  if (ownToken) {
    localStorage.setItem('resolv_token', ownToken);
    if (ownRefresh) localStorage.setItem('resolv_refresh_token', ownRefresh);
    window.location.href = '/admin';
  } else {
    window.location.href = '/login';
  }
}

// Drops impersonation bookkeeping without touching the active session —
// called on sign-out and on session-expiry redirects so a later sign-in in
// the same tab can never inherit a stale "viewing as" banner.
export function clearImpersonationMarkers() {
  sessionStorage.removeItem(ADMIN_TOKEN_KEY);
  sessionStorage.removeItem(ADMIN_REFRESH_KEY);
  sessionStorage.removeItem(IMPERSONATING_KEY);
}
