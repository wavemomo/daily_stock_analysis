import apiClient, { getCsrfToken } from './index';
import { API_BASE_URL } from '../utils/constants';
import { createApiError, parseApiError } from './error';

// 头像限制，与后端 wechat_miniapp_auth_service 保持一致（≤2MB，JPEG/PNG/WebP）。
export const AVATAR_MAX_BYTES = 2 * 1024 * 1024;
export const AVATAR_ACCEPT_TYPES = ['image/jpeg', 'image/png', 'image/webp'];

// 账户自助资料。后端为 /api/v1/account/*（中性前缀，Web Cookie 与小程序 Bearer 复用同一处理逻辑）。
export type AccountProfile = {
  id: number;
  nickname?: string | null;
  avatar_url?: string | null;
  profile_updated_at?: string | null;
  created_at?: string | null;
  last_login_at?: string | null;
  roles: string[];
  permissions: string[];
};

export type EmailBinding = {
  email?: string | null;
  email_verified: boolean;
  has_password: boolean;
  report_email_enabled: boolean;
};

export const accountApi = {
  async getProfile(): Promise<AccountProfile> {
    const { data } = await apiClient.get<AccountProfile>('/api/v1/account/me');
    return data;
  },

  async updateNickname(nickname: string): Promise<AccountProfile> {
    const { data } = await apiClient.patch<AccountProfile>('/api/v1/account/me', { nickname });
    return data;
  },

  // 头像为 multipart 上传，走原生 fetch 以让浏览器自动设置 boundary；手动带上 CSRF token 与 Cookie。
  async uploadAvatar(file: File): Promise<AccountProfile> {
    const form = new FormData();
    form.append('file', file);
    let response: Response;
    try {
      response = await fetch(`${API_BASE_URL}/api/v1/account/me/avatar`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'X-CSRF-Token': getCsrfToken() ?? '' },
        body: form,
      });
    } catch (err) {
      throw createApiError(parseApiError(err));
    }
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      throw createApiError(parseApiError({
        response: { status: response.status, data, statusText: response.statusText },
      }));
    }
    return data as AccountProfile;
  },

  async getEmailBinding(): Promise<EmailBinding> {
    const { data } = await apiClient.get<EmailBinding>('/api/v1/account/email');
    return data;
  },

  async setReportEmailEnabled(enabled: boolean): Promise<EmailBinding> {
    const { data } = await apiClient.patch<EmailBinding>('/api/v1/account/email/report-delivery', { enabled });
    return data;
  },

  async requestEmailCode(email: string): Promise<{ sent: boolean }> {
    const { data } = await apiClient.post<{ sent: boolean }>('/api/v1/account/email/request-code', { email });
    return data;
  },

  async bindEmail(email: string, code: string, password: string): Promise<EmailBinding> {
    const { data } = await apiClient.post<EmailBinding>('/api/v1/account/email/bind', { email, code, password });
    return data;
  },
};
