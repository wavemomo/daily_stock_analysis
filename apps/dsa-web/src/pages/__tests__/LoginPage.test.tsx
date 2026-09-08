import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import LoginPage from '../LoginPage';
import { WECHAT_OAUTH_START_PATH } from '../../api/webAuth';

const { navigate, useSearchParamsMock, useAuthMock, assign } = vi.hoisted(() => ({
  navigate: vi.fn(),
  useSearchParamsMock: vi.fn(),
  useAuthMock: vi.fn(),
  assign: vi.fn(),
}));

vi.mock('../../hooks', () => ({ useAuth: () => useAuthMock() }));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigate,
    useSearchParams: () => useSearchParamsMock(),
  };
});

describe('LoginPage', () => {
  const originalLocation = window.location;

  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    useAuthMock.mockReturnValue({ actor: 'anonymous' });
    useSearchParamsMock.mockReturnValue([new URLSearchParams('redirect=%2Fsettings')]);
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...originalLocation, origin: originalLocation.origin, assign },
    });
  });

  it('starts the official OAuth flow as a top-level navigation and preserves a safe return path', () => {
    render(<LoginPage />);

    fireEvent.click(screen.getByRole('button', { name: '前往微信扫码登录' }));

    expect(window.sessionStorage.getItem('dsa.webAuth.returnPath')).toBe('/settings');
    expect(assign).toHaveBeenCalledWith(WECHAT_OAUTH_START_PATH);
  });

  it.each([
    '//attacker.example',
    '/\\attacker.example',
    'https://attacker.example',
  ])('rejects unsafe redirect input (%s) before persisting the return path', (unsafeRedirect) => {
    useSearchParamsMock.mockReturnValue([new URLSearchParams({ redirect: unsafeRedirect })]);

    render(<LoginPage />);
    fireEvent.click(screen.getByRole('button', { name: '前往微信扫码登录' }));

    expect(window.sessionStorage.getItem('dsa.webAuth.returnPath')).toBe('/');
  });

  it('keeps a validated query redirect when no stored return path exists after the OAuth callback', async () => {
    useSearchParamsMock.mockReturnValue([new URLSearchParams({ redirect: '/settings?x=1#anchor' })]);
    useAuthMock.mockReturnValue({ actor: 'web_user' });

    render(<LoginPage />);

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/settings?x=1#anchor', { replace: true }));
  });

  it('consumes a stored safe return path with its query and hash after the OAuth callback', async () => {
    window.sessionStorage.setItem('dsa.webAuth.returnPath', '/settings?x=1#anchor');
    useAuthMock.mockReturnValue({ actor: 'web_user' });

    render(<LoginPage />);

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/settings?x=1#anchor', { replace: true }));
    expect(window.sessionStorage.getItem('dsa.webAuth.returnPath')).toBeNull();
  });

  it('consumes the validated return path after the OAuth callback restores a session', async () => {
    window.sessionStorage.setItem('dsa.webAuth.returnPath', '/access-control');
    useAuthMock.mockReturnValue({ actor: 'web_user' });

    render(<LoginPage />);

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/access-control', { replace: true }));
    expect(window.sessionStorage.getItem('dsa.webAuth.returnPath')).toBeNull();
  });
});
