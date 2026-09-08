import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createParsedApiError } from './api/error';
import App from './App';
import * as AuthContext from './contexts/AuthContext';
import { UI_LANGUAGE_STORAGE_KEY } from './utils/uiLanguage';

type AuthState = ReturnType<typeof AuthContext.useAuth>;

const { setCurrentRoute, useAgentChatStoreMock } = vi.hoisted(() => {
  const setCurrentRoute = vi.fn();
  const state = { completionBadge: false };
  const useAgentChatStoreMock = Object.assign(
    vi.fn((selector?: (value: typeof state) => unknown) => (selector ? selector(state) : state)),
    { getState: () => ({ setCurrentRoute }) },
  );
  return { setCurrentRoute, useAgentChatStoreMock };
});

vi.mock('./contexts/AuthContext', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => children,
  useAuth: vi.fn(),
}));

vi.mock('./contexts/FeatureQuotaContext', () => ({
  FeatureQuotaProvider: ({ children }: { children: ReactNode }) => children,
  useFeatureQuotas: () => ({ items: [], isLoading: false, refresh: vi.fn(), describe: () => '暂无额度信息' }),
}));

vi.mock('./stores/agentChatStore', () => ({ useAgentChatStore: useAgentChatStoreMock }));

vi.mock('./pages/HomePage', () => ({ default: () => <div data-testid="Home-page">Home</div> }));
vi.mock('./pages/ChatPage', () => ({ default: () => <div data-testid="Chat-page">Chat</div> }));
vi.mock('./pages/PortfolioPage', () => ({ default: () => <div data-testid="Portfolio-page">Portfolio</div> }));
vi.mock('./pages/DecisionSignalsPage', () => ({ default: () => <div data-testid="Decision signals-page">Decision signals</div> }));
vi.mock('./pages/BacktestPage', () => ({ default: () => <div data-testid="Backtest-page">Backtest</div> }));
vi.mock('./pages/AlertsPage', () => ({ default: () => <div data-testid="Alerts-page">Alerts</div> }));
vi.mock('./pages/TokenUsagePage', () => ({ default: () => <div data-testid="Usage-page">Usage</div> }));
vi.mock('./pages/FeatureQuotasPage', () => ({ default: () => <div data-testid="Feature quotas-page">Feature quotas</div> }));
vi.mock('./pages/StockScreeningPage', () => ({ default: () => <div data-testid="Screening-page">Screening</div> }));
vi.mock('./pages/SettingsPage', () => ({ default: () => <div data-testid="Settings-page">Settings</div> }));
vi.mock('./pages/AccessControlPage', () => ({ default: () => <div data-testid="Access control-page">Access control</div> }));
vi.mock('./pages/NotFoundPage', () => ({ default: () => <div data-testid="Not Found-page">Not Found</div> }));
vi.mock('./pages/LoginPage', () => ({ default: () => <div data-testid="login-page">Login</div> }));

function makeAuthState(overrides: Partial<AuthState> = {}): AuthState {
  const permissions = new Set<string>();
  return {
    actor: 'web_user',
    user: { id: 7, permissions: [] },
    loggedIn: true,
    isLoading: false,
    loadError: null,
    hasPermission: (permission) => permissions.has(permission),
    hasAnyPermission: (requested) => requested.some((permission) => permissions.has(permission)),
    logout: vi.fn().mockResolvedValue(undefined),
    refreshStatus: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.pushState({}, '', '/');
  localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'zh');
  vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState());
});

describe('App OAuth route guards', () => {
  it('shows the loading fallback while the OAuth session is restoring', () => {
    vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState({ isLoading: true }));

    const { container } = render(<App />);

    expect(container.querySelector('.border-t-cyan')).toBeInTheDocument();
  });

  it('redirects anonymous users to OAuth login with the original path', async () => {
    vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState({ actor: 'anonymous', user: null, loggedIn: false }));
    window.history.pushState({}, '', '/portfolio?tab=overview');

    render(<App />);

    expect(await screen.findByTestId('login-page')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/login');
    expect(window.location.search).toBe('?redirect=%2Fportfolio%3Ftab%3Doverview');
  });

  it('routes an authenticated user to regular pages', async () => {
    window.history.pushState({}, '', '/chat');

    render(<App />);

    expect(await screen.findByTestId('Chat-page')).toBeInTheDocument();
    expect(setCurrentRoute).toHaveBeenCalledWith('/chat');
  });

  it('allows settings only with system.manage', async () => {
    vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState({
      hasPermission: (permission) => permission === 'system.manage',
    }));
    window.history.pushState({}, '', '/settings');

    render(<App />);

    expect(await screen.findByTestId('Settings-page')).toBeInTheDocument();
  });

  it('redirects settings to home when system.manage is missing', async () => {
    window.history.pushState({}, '', '/settings');

    render(<App />);

    expect(await screen.findByTestId('Home-page')).toBeInTheDocument();
  });

  it('allows access control only with rbac.manage', async () => {
    vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState({
      hasPermission: (permission) => permission === 'rbac.manage',
    }));
    window.history.pushState({}, '', '/access-control');

    render(<App />);

    expect(await screen.findByTestId('Access control-page')).toBeInTheDocument();
  });

  it('redirects an authenticated visit to /login back to home', async () => {
    window.history.pushState({}, '', '/login');

    render(<App />);

    await waitFor(() => expect(screen.getByTestId('Home-page')).toBeInTheDocument());
  });

  it('renders a retryable alert for non-auth OAuth session failures', () => {
    const refreshStatus = vi.fn().mockResolvedValue(undefined);
    vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState({
      loadError: createParsedApiError({
        title: '请求失败', message: '服务暂不可用', rawMessage: '服务暂不可用', status: 503, category: 'http_error',
      }),
      refreshStatus,
    }));

    render(<App />);

    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });
});
