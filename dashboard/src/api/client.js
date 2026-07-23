import axios from 'axios';

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'https://hackathonn5.onrender.com',
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

client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !redirecting) {
      // Only redirect if the user has (or had) a token — avoids redirect loops on public endpoints
      const hadToken = !!localStorage.getItem('resolv_token');
      if (hadToken) {
        redirecting = true;
        localStorage.removeItem('resolv_token');
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
