import { describe, expect, it, vi } from 'vitest';

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock('../index', () => ({
  default: { get, post },
}));

import { webAuthApi } from '../webAuth';

describe('webAuthApi', () => {
  it('uses the Cookie-backed session endpoint without exposing OAuth credentials to JavaScript', async () => {
    get.mockResolvedValueOnce({ data: { user: { id: 7, permissions: ['agent.read'] }, csrf_token: 'csrf-token' } });

    await expect(webAuthApi.me()).resolves.toEqual({ user: { id: 7, permissions: ['agent.read'] }, csrf_token: 'csrf-token' });
    expect(get).toHaveBeenCalledWith('/api/v1/web-auth/me');
  });

  it('logs out through the single Web session endpoint', async () => {
    post.mockResolvedValueOnce({});

    await webAuthApi.logout();

    expect(post).toHaveBeenCalledWith('/api/v1/web-auth/logout');
  });

  it('signs in with email and password against the password login endpoint', async () => {
    post.mockResolvedValueOnce({ data: { user: { id: 7, permissions: [] }, csrf_token: 'csrf-token' } });

    await expect(webAuthApi.passwordLogin('user@example.com', 'secret-pass')).resolves.toEqual({
      user: { id: 7, permissions: [] },
      csrf_token: 'csrf-token',
    });
    expect(post).toHaveBeenCalledWith('/api/v1/web-auth/password/login', {
      email: 'user@example.com',
      password: 'secret-pass',
    });
  });
});
