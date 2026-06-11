import axios from 'axios';

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'https://hackathonn5.onrender.com',
  timeout: 30000,
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

export default client;
