import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';
import TokenUsagePage from '../TokenUsagePage';

const { get } = vi.hoisted(() => ({
  get: vi.fn(),
}));

const { useAuthMock } = vi.hoisted(() => ({
  useAuthMock: vi.fn(),
}));

vi.mock('../../api/index', () => ({
  default: { get },
}));

vi.mock('../../hooks', () => ({
  useAuth: useAuthMock,
}));

const dashboardResponse = {
  period: 'month',
  from_date: '2026-06-01',
  to_date: '2026-06-11',
  total_calls: 3,
  total_prompt_tokens: 120,
  total_completion_tokens: 280,
  total_tokens: 400,
  by_call_type: [
    {
      call_type: 'analysis',
      calls: 2,
      prompt_tokens: 100,
      completion_tokens: 200,
      total_tokens: 300,
    },
    {
      call_type: 'agent',
      calls: 1,
      prompt_tokens: 20,
      completion_tokens: 80,
      total_tokens: 100,
    },
  ],
  by_model: [
    {
      model: 'openai/gpt-test',
      calls: 2,
      prompt_tokens: 100,
      completion_tokens: 200,
      total_tokens: 300,
      max_total_tokens: 240,
    },
    {
      model: 'custom-router',
      calls: 1,
      prompt_tokens: 20,
      completion_tokens: 80,
      total_tokens: 100,
      max_total_tokens: 100,
    },
  ],
  recent_calls: [
    {
      id: 1,
      called_at: '2026-06-11T09:30:00',
      call_type: 'analysis',
      model: 'openai/gpt-test',
      stock_code: '600519',
      prompt_tokens: 40,
      completion_tokens: 200,
      total_tokens: 240,
    },
  ],
};

const byUserResponse = {
  period: 'month',
  from_date: '2026-06-01',
  to_date: '2026-06-11',
  scope: 'platform',
  owners: [
    {
      user_id: 42,
      nickname: 'Alice',
      owner_scope: 'user',
      calls: 3,
      prompt_tokens: 100,
      completion_tokens: 200,
      total_tokens: 300,
      last_called_at: '2026-06-11T09:30:00',
    },
    {
      user_id: null,
      nickname: null,
      owner_scope: 'global',
      calls: 1,
      prompt_tokens: 20,
      completion_tokens: 80,
      total_tokens: 100,
      last_called_at: null,
    },
  ],
};

/** 按 URL 分派：平台视图会同时请求 dashboard 与 by-user。 */
function mockUsageEndpoints() {
  get.mockImplementation((url: string) => {
    if (url === '/api/v1/usage/by-user') {
      return Promise.resolve({ data: byUserResponse });
    }
    return Promise.resolve({ data: dashboardResponse });
  });
}

function makeDashboardResponse(overrides: Partial<typeof dashboardResponse> = {}) {
  return {
    ...dashboardResponse,
    ...overrides,
  };
}

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

function renderPage() {
  return render(
    <UiLanguageProvider>
      <TokenUsagePage />
    </UiLanguageProvider>
  );
}

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem('dsa.uiLanguage', 'zh');
  vi.clearAllMocks();
  mockUsageEndpoints();
  // 默认按普通成员渲染：只允许查看本人用量。
  useAuthMock.mockReturnValue({ hasPermission: () => false });
});

describe('TokenUsagePage', () => {
  it('renders token summary, model breakdowns, and recent calls from the dashboard API shape', async () => {
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Token 用量监控' })).toBeInTheDocument();
    expect(await screen.findByText('400')).toBeInTheDocument();
    expect(screen.getAllByText('openai/gpt-test')).toHaveLength(2);
    expect(screen.getAllByText('个股分析')).toHaveLength(2);
    expect(screen.getByText(/600519/)).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith('/api/v1/usage/me/dashboard', {
      params: { period: 'month', limit: 50 },
    });
  });

  it('requests only the caller own usage for members without usage.read', async () => {
    useAuthMock.mockReturnValue({ hasPermission: (permission: string) => permission !== 'usage.read' });

    renderPage();

    await screen.findByRole('heading', { name: 'Token 用量监控' });
    expect(get).toHaveBeenCalledWith('/api/v1/usage/me/dashboard', {
      params: { period: 'month', limit: 50 },
    });
    expect(get).not.toHaveBeenCalledWith('/api/v1/usage/dashboard', expect.anything());
  });

  it('requests platform-wide usage for operators holding usage.read', async () => {
    useAuthMock.mockReturnValue({ hasPermission: (permission: string) => permission === 'usage.read' });

    renderPage();

    await screen.findByRole('heading', { name: 'Token 用量监控' });
    expect(get).toHaveBeenCalledWith('/api/v1/usage/dashboard', {
      params: { period: 'month', limit: 50 },
    });
    expect(get).toHaveBeenCalledWith('/api/v1/usage/by-user', {
      params: { period: 'month', limit: 100 },
    });
    expect(get).not.toHaveBeenCalledWith('/api/v1/usage/me/dashboard', expect.anything());
  });

  it('labels the current scope so members know they are seeing their own usage', async () => {
    renderPage();

    await screen.findByRole('heading', { name: 'Token 用量监控' });
    expect(screen.getByText(/当前仅展示你自己触发的用量/)).toBeInTheDocument();
    // 成员没有 usage.read，不应出现视图切换器。
    expect(screen.queryByRole('button', { name: '全平台' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '我的用量' })).not.toBeInTheDocument();
  });

  it('lets an operator switch back to their own usage view', async () => {
    useAuthMock.mockReturnValue({ hasPermission: (permission: string) => permission === 'usage.read' });

    renderPage();

    await screen.findByRole('heading', { name: 'Token 用量监控' });
    expect(screen.getByText(/当前展示全体用户的合计用量/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '我的用量' }));

    await waitFor(() => {
      expect(get).toHaveBeenCalledWith('/api/v1/usage/me/dashboard', {
        params: { period: 'month', limit: 50 },
      });
    });
    expect(screen.getByText(/当前仅展示你自己触发的用量/)).toBeInTheDocument();
  });

  it('renders per-user usage rows for the platform view', async () => {
    useAuthMock.mockReturnValue({ hasPermission: (permission: string) => permission === 'usage.read' });

    renderPage();

    expect(await screen.findByRole('heading', { name: '按用户用量' })).toBeInTheDocument();
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('#42')).toBeInTheDocument();
    // 不归属用户的后台消耗单列一行，不摊到具体用户。
    expect(screen.getByText('平台任务（定时分析 / 大盘复盘 / 后台）')).toBeInTheDocument();
  });

  it('hides the per-user table from members', async () => {
    renderPage();

    await screen.findByRole('heading', { name: 'Token 用量监控' });
    expect(screen.queryByRole('heading', { name: '按用户用量' })).not.toBeInTheDocument();
    expect(get).not.toHaveBeenCalledWith('/api/v1/usage/by-user', expect.anything());
  });

  it('renders English copy when the UI language is English', async () => {
    window.localStorage.setItem('dsa.uiLanguage', 'en');

    renderPage();

    expect(await screen.findByRole('heading', { name: 'Token usage' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Today' })).toBeInTheDocument();
    expect(screen.getAllByText('Stock analysis')).toHaveLength(2);
    expect(screen.getByText('Latest 50 LLM token audit records.')).toBeInTheDocument();
    expect(screen.queryByText('Token 用量监控')).not.toBeInTheDocument();
  });

  it('keeps the newest period data when dashboard requests resolve out of order', async () => {
    const monthRequest = createDeferred<{ data: typeof dashboardResponse }>();
    const todayRequest = createDeferred<{ data: typeof dashboardResponse }>();
    const todayResponse = makeDashboardResponse({
      period: 'today',
      from_date: '2026-06-15',
      to_date: '2026-06-15',
      total_calls: 9,
      total_prompt_tokens: 700,
      total_completion_tokens: 200,
      total_tokens: 900,
      by_call_type: [
        {
          call_type: 'analysis',
          calls: 9,
          prompt_tokens: 700,
          completion_tokens: 200,
          total_tokens: 900,
        },
      ],
      by_model: [
        {
          model: 'openai/gpt-test',
          calls: 9,
          prompt_tokens: 700,
          completion_tokens: 200,
          total_tokens: 900,
          max_total_tokens: 300,
        },
      ],
      recent_calls: [],
    });

    get.mockImplementation((_url, config) => {
      const period = config?.params?.period;
      if (period === 'month') {
        return monthRequest.promise;
      }
      if (period === 'today') {
        return todayRequest.promise;
      }
      return Promise.resolve({ data: dashboardResponse });
    });

    renderPage();

    await waitFor(() => {
      expect(get).toHaveBeenCalledWith('/api/v1/usage/me/dashboard', {
        params: { period: 'month', limit: 50 },
      });
    });

    fireEvent.click(screen.getByRole('button', { name: '今日' }));

    await waitFor(() => {
      expect(get).toHaveBeenLastCalledWith('/api/v1/usage/me/dashboard', {
        params: { period: 'today', limit: 50 },
      });
    });

    await act(async () => {
      todayRequest.resolve({ data: todayResponse });
    });

    expect(await screen.findByText('900')).toBeInTheDocument();

    await act(async () => {
      monthRequest.resolve({ data: dashboardResponse });
    });

    await waitFor(() => {
      expect(screen.getByText('900')).toBeInTheDocument();
    });
    expect(screen.queryByText('400')).not.toBeInTheDocument();
  });

  it('reloads dashboard when period changes', async () => {
    renderPage();

    await screen.findByRole('heading', { name: 'Token 用量监控' });
    fireEvent.click(screen.getByRole('button', { name: '今日' }));

    await waitFor(() => {
      expect(get).toHaveBeenLastCalledWith('/api/v1/usage/me/dashboard', {
        params: { period: 'today', limit: 50 },
      });
    });
  });
});
