import axios from 'axios';
import { setLoggedInCookie, clearLoggedInCookie } from './sessionCookie';

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'https://backend.tresolv.online',
  timeout: 35000, // covers Render free-tier cold start (~30s)
});

client.interceptors.request.use((config) => {
  const token = localStorage.getItem('resolv_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let redirecting = false;
let refreshPromise = null;

// Supabase access tokens are short-lived (typically 1hr, vs. the old
// custom JWT's 24hr) — a silent refresh-and-retry-once on 401 keeps a
// signed-in user signed in across normal usage instead of bouncing them to
// /login every time the access token expires.
function refreshSession() {
  const refreshToken = localStorage.getItem('resolv_refresh_token');
  if (!refreshToken) return Promise.resolve(false);

  if (!refreshPromise) {
    refreshPromise = axios
      .post(`${client.defaults.baseURL}/api/v1/auth/refresh`, { refresh_token: refreshToken }, { timeout: 35000 })
      .then((res) => {
        const { access_token, refresh_token, expires_in } = res.data || {};
        if (!access_token) throw new Error('No access token in refresh response');
        localStorage.setItem('resolv_token', access_token);
        if (refresh_token) localStorage.setItem('resolv_refresh_token', refresh_token);
        // Slides forward with every successful silent refresh, so the
        // marketing-site flag stays accurate for as long as the session
        // keeps renewing itself.
        setLoggedInCookie(expires_in);
        return true;
      })
      .catch(() => {
        // The refresh token itself was invalid/expired — the session is
        // genuinely over, not just the access token. Clear the flag so the
        // marketing site never shows "Dashboard" for a session that's
        // actually gone.
        localStorage.removeItem('resolv_token');
        localStorage.removeItem('resolv_refresh_token');
        clearLoggedInCookie();
        return false;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

client.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;

    if (error.response?.status === 401 && original && !original._retried && localStorage.getItem('resolv_refresh_token')) {
      original._retried = true;
      const refreshed = await refreshSession();
      if (refreshed) {
        return client(original);
      }
    }

    if (error.response?.status === 401 && !redirecting) {
      // Only redirect if the user has (or had) a token — avoids redirect loops on public endpoints
      const hadToken = !!localStorage.getItem('resolv_token');
      if (hadToken) {
        redirecting = true;
        localStorage.removeItem('resolv_token');
        localStorage.removeItem('resolv_refresh_token');
        clearLoggedInCookie();
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  }
);

// Plan/usage-limit endpoints return a structured 402 body
// ({error, resource, current, limit, upgrade_required}) instead of a plain
// string `detail`. Rendering that object directly as JSX text crashes React
// ("Objects are not valid as a React child") — always go through this to get
// a safe, friendly string regardless of which shape the backend sent.
export function extractErrorMessage(err, fallback = 'Something went wrong.') {
  const detail = err?.response?.data?.detail;
  if (!detail) return err?.response?.data?.error || fallback;
  if (typeof detail === 'string') return detail;
  if (detail.error === 'PLAN_LIMIT_REACHED') {
    const resource = (detail.resource || 'this').replace(/_/g, ' ');
    return `Your plan allows ${detail.limit} ${resource}. Upgrade to continue.`;
  }
  return detail.message || fallback;
}

export default client;
