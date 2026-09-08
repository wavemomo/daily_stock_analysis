import apiClient from './index';

export const WECHAT_OAUTH_START_PATH = '/api/v1/web-auth/wechat/start';

export type WebUser = {
  id: number;
  nickname?: string | null;
  avatar_url?: string | null;
  roles?: string[];
  permissions?: string[];
};

export type WebUserSessionResponse = {
  user: WebUser;
  csrf_token: string;
};

export const webAuthApi = {
  async me(): Promise<WebUserSessionResponse> {
    const { data } = await apiClient.get<WebUserSessionResponse>('/api/v1/web-auth/me');
    return data;
  },

  async logout(): Promise<void> {
    await apiClient.post('/api/v1/web-auth/logout');
  },
};
