import apiClient from './index';
import { toCamelCase } from './utils';

export type UsagePeriod = 'today' | 'month' | 'all';

export type UsageCallTypeBreakdown = {
  callType: string;
  calls: number;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
};

export type UsageModelBreakdown = {
  model: string;
  calls: number;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  maxTotalTokens: number;
};

export type UsageCallRecord = {
  id: number;
  calledAt: string;
  callType: string;
  model: string;
  stockCode?: string | null;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
};

export type UsageDashboard = {
  period: UsagePeriod;
  fromDate: string;
  toDate: string;
  totalCalls: number;
  totalPromptTokens: number;
  totalCompletionTokens: number;
  totalTokens: number;
  byCallType: UsageCallTypeBreakdown[];
  byModel: UsageModelBreakdown[];
  recentCalls: UsageCallRecord[];
};

export const usageApi = {
  /**
   * 拉取 Token 用量看板。
   *
   * 多用户隔离：`scope: 'platform'` 走 `/api/v1/usage/dashboard`（跨全体用户聚合，
   * 需 `usage.read`，仅运营/管理员）；`scope: 'self'`（默认）走 `/api/v1/usage/me/dashboard`，
   * 只返回当前登录用户自己的用量，普通成员可见。
   */
  getDashboard: async (
    params: { period?: UsagePeriod; limit?: number; scope?: 'self' | 'platform' } = {},
  ): Promise<UsageDashboard> => {
    const path =
      params.scope === 'platform' ? '/api/v1/usage/dashboard' : '/api/v1/usage/me/dashboard';
    const response = await apiClient.get<Record<string, unknown>>(path, {
      params: {
        period: params.period ?? 'month',
        limit: params.limit ?? 50,
      },
    });

    return toCamelCase<UsageDashboard>(response.data);
  },
};
