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

/** 当前视图作用域：本人用量 vs 跨用户平台聚合。 */
export type UsageScope = 'self' | 'platform';

export type UsageOwnerBreakdown = {
  /** 归属用户 id；平台/后台消耗合并条目为 null。 */
  userId: number | null;
  nickname: string | null;
  ownerScope: 'user' | 'global';
  calls: number;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  lastCalledAt: string | null;
};

export type UsageByUser = {
  period: UsagePeriod;
  fromDate: string;
  toDate: string;
  scope: UsageScope;
  owners: UsageOwnerBreakdown[];
};

export type UsageDashboard = {
  period: UsagePeriod;
  fromDate: string;
  toDate: string;
  /** 由后端回传，前端据此标注当前是本人视图还是全平台聚合。 */
  scope: UsageScope;
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
    params: { period?: UsagePeriod; limit?: number; scope?: UsageScope } = {},
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

  /**
   * 平台级「按用户」用量下钻，需 `usage.read`（运营/管理员）。
   *
   * 定时任务、大盘复盘与后台扇出等不归属任何用户的消耗会合并为一条
   * `ownerScope: 'global'` 条目，不会摊到具体用户头上。
   */
  getByUser: async (
    params: { period?: UsagePeriod; limit?: number } = {},
  ): Promise<UsageByUser> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/usage/by-user', {
      params: {
        period: params.period ?? 'month',
        limit: params.limit ?? 100,
      },
    });

    return toCamelCase<UsageByUser>(response.data);
  },
};
