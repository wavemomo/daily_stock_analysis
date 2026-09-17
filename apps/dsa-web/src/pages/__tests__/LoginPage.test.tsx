import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import LoginPage from '../LoginPage';

const { navigate, useSearchParamsMock, useAuthMock } = vi.hoisted(() => ({
  navigate: vi.fn(),
  useSearchParamsMock: vi.fn(),
  useAuthMock: vi.fn(),
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
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    useAuthMock.mockReturnValue({ actor: 'anonymous' });
    useSearchParamsMock.mockReturnValue([new URLSearchParams('redirect=%2Fsettings')]);
  });

  it('presents the email/password form and no longer offers WeChat QR-code sign-in', () => {
    render(<LoginPage />);

    expect(screen.getByRole('button', { name: '登录' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('you@example.com')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('请输入密码')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '前往微信扫码登录' })).toBeNull();
  });

  it('keeps a validated query redirect when no stored return path exists after login', async () => {
    useSearchParamsMock.mockReturnValue([new URLSearchParams({ redirect: '/settings?x=1#anchor' })]);
    useAuthMock.mockReturnValue({ actor: 'web_user' });

    render(<LoginPage />);

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/settings?x=1#anchor', { replace: true }));
  });

  it('consumes a stored safe return path with its query and hash after login', async () => {
    window.sessionStorage.setItem('dsa.webAuth.returnPath', '/settings?x=1#anchor');
    useAuthMock.mockReturnValue({ actor: 'web_user' });

    render(<LoginPage />);

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/settings?x=1#anchor', { replace: true }));
    expect(window.sessionStorage.getItem('dsa.webAuth.returnPath')).toBeNull();
  });

  it('consumes the validated return path after a session is established', async () => {
    window.sessionStorage.setItem('dsa.webAuth.returnPath', '/access-control');
    useAuthMock.mockReturnValue({ actor: 'web_user' });

    render(<LoginPage />);

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/access-control', { replace: true }));
    expect(window.sessionStorage.getItem('dsa.webAuth.returnPath')).toBeNull();
  });
});
