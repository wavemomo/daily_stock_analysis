import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { createApiError, createParsedApiError } from '../../../api/error';
import { SidebarNav } from '../SidebarNav';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';

const mockLogout = vi.fn().mockResolvedValue(undefined);
const mockNavigate = vi.fn();
const mockGetScreeningStatus = vi.fn().mockResolvedValue({ enabled: false, available: false });
const mockThemeToggle = vi.fn(({ collapsed }: { collapsed?: boolean }) => (
  <button type="button">{collapsed ? '切换主题(折叠)' : '切换主题'}</button>
));

const completionBadgeState = { value: true };
const featureQuotaState = { hasLoadError: false };
const authState: { actor: 'anonymous' | 'web_user'; permissions: string[] } = {
  actor: 'web_user',
  permissions: ['agent.read', 'screening.read', 'portfolio.read', 'decision_signals.read', 'backtest.read', 'alerts.read', 'usage.read'],
};
const mockRefreshQuotas = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({
    actor: authState.actor,
    hasAnyPermission: (requested: string[]) => requested.some((permission) => authState.permissions.includes(permission)),
    logout: mockLogout,
  }),
}));

vi.mock('../../../contexts/FeatureQuotaContext', () => ({
  useFeatureQuotas: () => ({
    items: [],
    isLoading: false,
    hasLoadError: featureQuotaState.hasLoadError,
    refresh: mockRefreshQuotas,
    describe: () => '股票分析 4 · AI 问股 2',
  }),
}));

vi.mock('../../../stores/agentChatStore', () => ({
  useAgentChatStore: (selector: (state: { completionBadge: boolean }) => unknown) =>
    selector({ completionBadge: completionBadgeState.value }),
}));

vi.mock('../../../api/screening', () => ({
  SCREENING_CONFIG_CHANGED_EVENT: 'screening-config-changed',
  SYSTEM_CONFIG_CHANGED_EVENT: 'dsa-system-config-changed',
  screeningApi: {
    getStatus: () => mockGetScreeningStatus(),
  },
}));

vi.mock('../../theme/ThemeToggle', () => ({
  ThemeToggle: (props: { collapsed?: boolean }) => mockThemeToggle(props),
}));

describe('SidebarNav', () => {
  beforeEach(() => {
    mockLogout.mockReset().mockResolvedValue(undefined);
    mockNavigate.mockReset();
    mockGetScreeningStatus.mockReset().mockResolvedValue({ enabled: false, available: false });
    featureQuotaState.hasLoadError = false;
    authState.actor = 'web_user';
    authState.permissions = ['agent.read', 'screening.read', 'portfolio.read', 'decision_signals.read', 'backtest.read', 'alerts.read', 'usage.read'];
    completionBadgeState.value = true;
  });

  it('renders the current feature-quota summary for an authenticated user', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.getByText('股票分析 4 · AI 问股 2')).toBeInTheDocument();
  });

  it('hides the screening navigation item while Screening is disabled', () => {
    mockGetScreeningStatus.mockResolvedValueOnce({ enabled: false, available: true });

    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('link', { name: '选股' })).not.toBeInTheDocument();
  });

  it('shows screening directly after chat when Screening is enabled', async () => {
    mockGetScreeningStatus.mockResolvedValueOnce({ enabled: true, available: true });

    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('link', { name: '选股' })).toHaveAttribute('href', '/screening');
    const hrefs = screen.getAllByRole('link').map((link) => link.getAttribute('href'));
    expect(hrefs.slice(0, 5)).toEqual(['/', '/chat', '/screening', '/portfolio', '/decision-signals']);
  });

  it('refreshes the controlled screening entry after config changes', async () => {
    mockGetScreeningStatus
      .mockResolvedValueOnce({ enabled: false, available: true })
      .mockResolvedValueOnce({ enabled: true, available: true });

    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('link', { name: '选股' })).not.toBeInTheDocument();
    window.dispatchEvent(new Event('screening-config-changed'));

    expect(await screen.findByRole('link', { name: '选股' })).toHaveAttribute('href', '/screening');
    await waitFor(() => expect(mockGetScreeningStatus.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it('shows the shared completion badge only when chat completion is pending', () => {
    completionBadgeState.value = true;

    const { rerender } = render(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.getByTestId('chat-completion-badge')).toBeInTheDocument();
    expect(screen.getByLabelText('问股有新消息')).toBeInTheDocument();

    completionBadgeState.value = false;
    rerender(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.queryByTestId('chat-completion-badge')).not.toBeInTheDocument();
  });

  it('renders the collapsed theme toggle variant when the sidebar is collapsed', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav collapsed />
      </MemoryRouter>,
    );

    expect(mockThemeToggle).toHaveBeenCalledWith(
      expect.objectContaining({ variant: 'nav', collapsed: true }),
    );
    expect(screen.getByRole('button', { name: '切换主题(折叠)' })).toBeInTheDocument();
  });

  it('renders the alerts navigation item and marks it active', () => {
    render(
      <MemoryRouter initialEntries={['/alerts']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    const alertsLink = screen.getByRole('link', { name: '告警' });
    expect(alertsLink).toHaveAttribute('href', '/alerts');
    expect(alertsLink).toHaveClass('font-medium');
  });

  it('renders the AI signals navigation item and marks it active', () => {
    render(
      <MemoryRouter initialEntries={['/decision-signals']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    const signalsLink = screen.getByRole('link', { name: 'AI 建议' });
    expect(signalsLink).toHaveAttribute('href', '/decision-signals');
    expect(signalsLink).toHaveClass('font-medium');
  });

  it('provides a dedicated feature quota navigation entry', () => {
    render(
      <MemoryRouter initialEntries={['/feature-quotas']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    const quotaLink = screen.getByRole('link', { name: '功能额度' });
    expect(quotaLink).toHaveAttribute('href', '/feature-quotas');
    expect(quotaLink).toHaveClass('font-medium');
  });

  it('opens the logout confirmation, logs out, and navigates to the web login page', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: '退出' }));

    expect(await screen.findByRole('heading', { name: '退出登录' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认退出' }));

    await waitFor(() => expect(mockLogout).toHaveBeenCalledTimes(1));
    expect(mockNavigate).toHaveBeenCalledWith('/login', { replace: true });
  });

  it('disables both dialog actions while logout is pending', async () => {
    let resolveLogout!: () => void;
    mockLogout.mockImplementationOnce(() => new Promise<void>((resolve) => {
      resolveLogout = resolve;
    }));

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: '退出' }));
    fireEvent.click(screen.getByRole('button', { name: '确认退出' }));

    const pendingButton = await screen.findByRole('button', { name: '退出中...' });
    expect(pendingButton).toBeDisabled();
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled();

    resolveLogout();
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/login', { replace: true }));
  });

  it('keeps the dialog open and shows a retryable error when logout fails', async () => {
    mockLogout.mockRejectedValueOnce(new Error('network failure'));

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: '退出' }));
    fireEvent.click(screen.getByRole('button', { name: '确认退出' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('退出失败，请检查网络后重试。');
    expect(screen.getByRole('heading', { name: '退出登录' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认退出' })).not.toBeDisabled();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('offers quota reload when the entitlement summary is stale', () => {
    featureQuotaState.hasLoadError = true;
    mockRefreshQuotas.mockReset();

    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: '重新加载额度' }));
    expect(mockRefreshQuotas).toHaveBeenCalledTimes(1);
    featureQuotaState.hasLoadError = false;
  });
});


describe('SidebarNav logout edge cases', () => {
  beforeEach(() => {
    mockLogout.mockReset().mockResolvedValue(undefined);
    mockNavigate.mockReset();
    mockGetScreeningStatus.mockReset().mockResolvedValue({ enabled: false, available: false });
    authState.actor = 'web_user';
    authState.permissions = [];
  });

  it('hides protected navigation entries when the canonical session lacks their permissions', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('link', { name: '问股' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '访问控制' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '设置' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: '首页' })).toBeInTheDocument();
  });

  it('shows administration navigation only for the corresponding permissions', () => {
    authState.permissions = ['rbac.manage', 'system.manage'];

    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '权限管理' })).toHaveAttribute('href', '/access-control');
    expect(screen.getByRole('link', { name: '设置' })).toHaveAttribute('href', '/settings');
  });

  it('keeps the logout dialog open with a refresh-and-retry message after csrf validation fails', async () => {
    mockLogout.mockRejectedValueOnce(createApiError(
      createParsedApiError({
        title: '请求失败',
        message: 'csrf_failed',
        rawMessage: 'csrf_failed',
        status: 403,
        category: 'http_error',
      }),
      { response: { status: 403, data: { error: 'csrf_failed' } } },
    ));

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: '退出' }));
    fireEvent.click(screen.getByRole('button', { name: '确认退出' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('会话校验失败，请刷新页面后重试退出。');
    expect(screen.getByRole('heading', { name: '退出登录' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认退出' })).not.toBeDisabled();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('renders regular-user logout controls and quota retry text in English', async () => {
    featureQuotaState.hasLoadError = true;
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'en');

    try {
      render(
        <UiLanguageProvider>
          <MemoryRouter initialEntries={['/chat']}>
            <SidebarNav />
          </MemoryRouter>
        </UiLanguageProvider>,
      );

      expect(screen.getByRole('button', { name: 'Reload quotas' })).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
      expect(await screen.findByRole('heading', { name: 'Log out' })).toBeInTheDocument();
      expect(screen.getByText('Logging out will revoke the unified signed-in session in this browser.')).toBeInTheDocument();
      expect(screen.queryByText('退出后将撤销当前浏览器中的统一登录会话。')).not.toBeInTheDocument();
    } finally {
      featureQuotaState.hasLoadError = false;
      window.localStorage.removeItem(UI_LANGUAGE_STORAGE_KEY);
    }
  });
});