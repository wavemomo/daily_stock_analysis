import { beforeEach, describe, expect, it, vi } from 'vitest';
import { usageApi } from '../usage';

const get = vi.hoisted(() => vi.fn());

vi.mock('../index', () => ({
  default: { get },
}));

describe('usageApi', () => {
  beforeEach(() => {
    get.mockReset();
  });

  it('requests dashboard data with period and limit query params and camelCases the response', async () => {
    get.mockResolvedValueOnce({
      data: {
        period: 'today',
        from_date: '2026-06-14',
        to_date: '2026-06-14',
        total_calls: 2,
        total_prompt_tokens: 30,
        total_completion_tokens: 70,
        total_tokens: 100,
        by_call_type: [
          {
            call_type: 'analysis',
            calls: 1,
            prompt_tokens: 10,
            completion_tokens: 40,
            total_tokens: 50,
          },
        ],
        by_model: [
          {
            model: 'minimax/MiniMax-M3',
            calls: 1,
            prompt_tokens: 10,
            completion_tokens: 40,
            total_tokens: 50,
            max_total_tokens: 50,
          },
        ],
        recent_calls: [
          {
            id: 7,
            called_at: '2026-06-14T09:30:00',
            call_type: 'analysis',
            model: 'minimax/MiniMax-M3',
            stock_code: '600519',
            prompt_tokens: 10,
            completion_tokens: 40,
            total_tokens: 50,
          },
        ],
      },
    });

    const result = await usageApi.getDashboard({ period: 'today', limit: 10 });

    expect(get).toHaveBeenCalledWith('/api/v1/usage/me/dashboard', {
      params: { period: 'today', limit: 10 },
    });
    expect(result.fromDate).toBe('2026-06-14');
    expect(result.totalPromptTokens).toBe(30);
    expect(result.byCallType[0].callType).toBe('analysis');
    expect(result.byModel[0].maxTotalTokens).toBe(50);
    expect(result.recentCalls[0].calledAt).toBe('2026-06-14T09:30:00');
    expect(result.recentCalls[0].stockCode).toBe('600519');
  });

  it('uses month and 50 as default dashboard query params', async () => {
    get.mockResolvedValueOnce({
      data: {
        period: 'month',
        from_date: '2026-06-01',
        to_date: '2026-06-14',
        total_calls: 0,
        total_prompt_tokens: 0,
        total_completion_tokens: 0,
        total_tokens: 0,
        by_call_type: [],
        by_model: [],
        recent_calls: [],
      },
    });

    await usageApi.getDashboard();

    expect(get).toHaveBeenCalledWith('/api/v1/usage/me/dashboard', {
      params: { period: 'month', limit: 50 },
    });
  });

  it('targets the platform-wide endpoint only when the platform scope is requested', async () => {
    get.mockResolvedValueOnce({
      data: {
        period: 'month',
        from_date: '2026-06-01',
        to_date: '2026-06-14',
        total_calls: 0,
        total_prompt_tokens: 0,
        total_completion_tokens: 0,
        total_tokens: 0,
        by_call_type: [],
        by_model: [],
        recent_calls: [],
      },
    });

    await usageApi.getDashboard({ scope: 'platform' });

    expect(get).toHaveBeenCalledWith('/api/v1/usage/dashboard', {
      params: { period: 'month', limit: 50 },
    });
  });

  it('exposes the scope reported by the backend so the UI can label the view', async () => {
    get.mockResolvedValueOnce({
      data: {
        period: 'month',
        from_date: '2026-06-01',
        to_date: '2026-06-14',
        scope: 'self',
        total_calls: 0,
        total_prompt_tokens: 0,
        total_completion_tokens: 0,
        total_tokens: 0,
        by_call_type: [],
        by_model: [],
        recent_calls: [],
      },
    });

    const result = await usageApi.getDashboard();

    expect(result.scope).toBe('self');
  });

  it('requests the per-user drill-down and camelCases owner rows', async () => {
    get.mockResolvedValueOnce({
      data: {
        period: 'month',
        from_date: '2026-06-01',
        to_date: '2026-06-14',
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
            last_called_at: '2026-06-14T09:30:00',
          },
          {
            user_id: null,
            nickname: null,
            owner_scope: 'global',
            calls: 1,
            prompt_tokens: 5,
            completion_tokens: 5,
            total_tokens: 10,
            last_called_at: null,
          },
        ],
      },
    });

    const result = await usageApi.getByUser({ period: 'month', limit: 25 });

    expect(get).toHaveBeenCalledWith('/api/v1/usage/by-user', {
      params: { period: 'month', limit: 25 },
    });
    expect(result.scope).toBe('platform');
    expect(result.owners[0].userId).toBe(42);
    expect(result.owners[0].ownerScope).toBe('user');
    expect(result.owners[0].lastCalledAt).toBe('2026-06-14T09:30:00');
    expect(result.owners[1].userId).toBeNull();
    expect(result.owners[1].ownerScope).toBe('global');
    expect(result.owners[1].lastCalledAt).toBeNull();
  });

  it('uses month and 100 as default per-user query params', async () => {
    get.mockResolvedValueOnce({
      data: {
        period: 'month',
        from_date: '2026-06-01',
        to_date: '2026-06-14',
        scope: 'platform',
        owners: [],
      },
    });

    await usageApi.getByUser();

    expect(get).toHaveBeenCalledWith('/api/v1/usage/by-user', {
      params: { period: 'month', limit: 100 },
    });
  });
});
