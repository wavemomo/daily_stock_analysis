import axios from 'axios';
import { API_BASE_URL } from '../utils/constants';
import { attachParsedApiError } from './error';

let csrfToken: string | undefined;

export function setCsrfToken(token?: string): void {
  csrfToken = token || undefined;
}

export const FEATURE_QUOTA_CHANGED_EVENT = 'dsa:feature-quota-changed';

export function notifyFeatureQuotaChanged(): void {
  window.dispatchEvent(new CustomEvent(FEATURE_QUOTA_CHANGED_EVENT));
}

function isUnsafeCookieRequest(method: string | undefined): boolean {
  return ['post', 'put', 'patch', 'delete'].includes((method ?? 'get').toLowerCase());
}

export function isSessionProbe(url: string | undefined): boolean {
  const normalizedUrl = (url ?? '').split('?')[0];
  return normalizedUrl === '/api/v1/web-auth/me'
    || normalizedUrl === '/api/v1/web-auth/logout';
}

function isLoginRoute(): boolean {
  return window.location.pathname === '/login';
}

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.request.use((config) => {
  if (csrfToken && isUnsafeCookieRequest(config.method)) {
    config.headers.set('X-CSRF-Token', csrfToken);
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => {
    if (isUnsafeCookieRequest(response.config.method) && response.status >= 200 && response.status < 300) {
      notifyFeatureQuotaChanged();
    }
    return response;
  },
  (error) => {
    if (error.response?.status === 401 && !isSessionProbe(error.config?.url) && !isLoginRoute()) {
      const path = window.location.pathname + window.location.search;
      const redirect = encodeURIComponent(path);
      window.location.assign(`/login?redirect=${redirect}`);
    }
    const parsed = attachParsedApiError(error);
    if (parsed.category === 'feature_quota_exceeded') {
      window.dispatchEvent(new CustomEvent('dsa:feature-quota-exceeded'));
    }
    return Promise.reject(error);
  }
);

export default apiClient;
