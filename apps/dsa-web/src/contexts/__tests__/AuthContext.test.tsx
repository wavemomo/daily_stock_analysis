import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createApiError, createParsedApiError } from '../../api/error';
import { AuthProvider, useAuth } from '../AuthContext';

const { webMe, webLogout, setCsrfToken, resetDashboardState } = vi.hoisted(() => ({
  webMe: vi.fn(),
  webLogout: vi.fn(),
  setCsrfToken: vi.fn(),
  resetDashboardState: vi.fn(),
}));

vi.mock('../../api/webAuth', () => ({
  webAuthApi: {
    me: webMe,
    logout: webLogout,
  },
}));

vi.mock('../../api', () => ({ setCsrfToken }));

vi.mock('../../stores', () => ({
  useStockPoolStore: {
    getState: () => ({ resetDashboardState }),
  },
}));

const unauthorized = () => createApiError(
  createParsedApiError({
    title: '未登录',
    message: 'Web login required',
    rawMessage: 'Web login required',
    status: 401,
    category: 'http_error',
  }),
  { response: { status: 401, data: { error: 'unauthorized' } } },
);

const Probe = () => {
  const auth = useAuth();
  return (
    <div>
      <span data-testid="actor">{auth.actor}</span>
      <span data-testid="status">{auth.loggedIn ? 'logged-in' : 'logged-out'}</span>
      <span data-testid="permission">{auth.hasPermission('system.manage') ? 'allowed' : 'denied'}</span>
      <span data-testid="any-permission">{auth.hasAnyPermission(['rbac.manage', 'system.manage']) ? 'allowed' : 'denied'}</span>
      <button type="button" onClick={() => void auth.refreshStatus()}>refresh</button>
      <button type="button" onClick={() => void auth.logout().catch(() => undefined)}>logout</button>
    </div>
  );
};

describe('AuthContext', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    webMe.mockRejectedValue(unauthorized());
  });

  it('restores the single OAuth cookie session and exposes canonical permissions', async () => {
    webMe.mockResolvedValueOnce({
      user: { id: 7, nickname: '投资者', roles: ['admin'], permissions: ['system.manage', 'rbac.manage'] },
      csrf_token: 'web-user-csrf-token',
    });

    render(<AuthProvider><Probe /></AuthProvider>);

    await waitFor(() => expect(screen.getByTestId('actor')).toHaveTextContent('web_user'));
    expect(screen.getByTestId('status')).toHaveTextContent('logged-in');
    expect(screen.getByTestId('permission')).toHaveTextContent('allowed');
    expect(screen.getByTestId('any-permission')).toHaveTextContent('allowed');
    expect(setCsrfToken).toHaveBeenCalledWith('web-user-csrf-token');
  });

  it('fails closed to anonymous on a missing session without exposing an error screen', async () => {
    render(<AuthProvider><Probe /></AuthProvider>);

    await waitFor(() => expect(screen.getByTestId('actor')).toHaveTextContent('anonymous'));
    expect(screen.getByTestId('permission')).toHaveTextContent('denied');
    expect(setCsrfToken).toHaveBeenCalledWith();
    expect(resetDashboardState).toHaveBeenCalled();
  });

  it('clears an established session when a refresh returns 401', async () => {
    webMe
      .mockResolvedValueOnce({ user: { id: 7, permissions: ['agent.read'] }, csrf_token: 'csrf-token' })
      .mockRejectedValueOnce(unauthorized());

    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => expect(screen.getByTestId('actor')).toHaveTextContent('web_user'));

    fireEvent.click(screen.getByRole('button', { name: 'refresh' }));

    await waitFor(() => expect(screen.getByTestId('actor')).toHaveTextContent('anonymous'));
    expect(setCsrfToken).toHaveBeenLastCalledWith();
  });

  it('treats a 401 logout as idempotent and always resets local state', async () => {
    webMe.mockResolvedValueOnce({ user: { id: 7, permissions: [] }, csrf_token: 'csrf-token' });
    webLogout.mockRejectedValueOnce(unauthorized());

    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => expect(screen.getByTestId('actor')).toHaveTextContent('web_user'));

    fireEvent.click(screen.getByRole('button', { name: 'logout' }));

    await waitFor(() => expect(screen.getByTestId('actor')).toHaveTextContent('anonymous'));
    expect(webLogout).toHaveBeenCalledOnce();
    expect(setCsrfToken).toHaveBeenLastCalledWith();
  });

  it('keeps non-401 logout failures observable after clearing local session state', async () => {
    webMe.mockResolvedValueOnce({ user: { id: 7, permissions: [] }, csrf_token: 'csrf-token' });
    webLogout.mockRejectedValueOnce(new Error('network failure'));

    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => expect(screen.getByTestId('actor')).toHaveTextContent('web_user'));

    fireEvent.click(screen.getByRole('button', { name: 'logout' }));

    await waitFor(() => expect(screen.getByTestId('actor')).toHaveTextContent('anonymous'));
  });
});
