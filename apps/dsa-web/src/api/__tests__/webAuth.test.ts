import { describe, expect, it, vi } from 'vitest';

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock('../index', () => ({
  default: { get, post },
}));

import { WECHAT_OAUTH_START_PATH, webAuthApi } from '../webAuth';

describe('webAuthApi', () => {
  it('uses the Cookie-backed session endpoint without exposing OAuth credentials to JavaScript', async () => {
    get.mockResolvedValueOnce({ data: { user: { id: 7, permissions: ['agent.read'] }, csrf_token: 'csrf-token' } });

    await expect(webAuthApi.me()).resolves.toEqual({ user: { id: 7, permissions: ['agent.read'] }, csrf_token: 'csrf-token' });
    expect(get).toHaveBeenCalledWith('/api/v1/web-auth/me');
  });

  it('logs out through the single Web OAuth session endpoint', async () => {
    post.mockResolvedValueOnce({});

    await webAuthApi.logout();

    expect(post).toHaveBeenCalledWith('/api/v1/web-auth/logout');
    expect(WECHAT_OAUTH_START_PATH).toBe('/api/v1/web-auth/wechat/start');
  });
});
