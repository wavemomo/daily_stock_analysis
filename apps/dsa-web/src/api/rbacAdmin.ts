import apiClient from './index';
import { toCamelCase } from './utils';

export type RbacPermission = {
  code: string;
  groupCode: string;
  description: string;
};

export type RbacRole = {
  code: string;
  name: string;
  description: string;
  isSystem: boolean;
  permissions: string[];
};

export type RbacUser = {
  id: number;
  nickname?: string | null;
  avatarUrl?: string | null;
  createdAt?: string | null;
  lastLoginAt?: string | null;
  isActive: boolean;
  roles: string[];
  permissions: string[];
};

export type Paginated<T> = {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
};

export type RbacAuditEvent = {
  id: number;
  action: string;
  targetType: string;
  targetId: string;
  actorUserId?: number | null;
  metadata: Record<string, unknown>;
  createdAt?: string | null;
};

export type FeatureQuotaPolicy = {
  featureCode: string;
  name: string;
  description: string;
  dailyLimit: number;
  updatedAt?: string | null;
  updatedByUserId?: number | null;
};

export type FeatureQuotaPlanLimit = {
  featureCode: string;
  dailyLimit: number;
};

export type FeatureQuotaPlan = {
  code: string;
  name: string;
  description: string;
  isActive: boolean;
  limits: FeatureQuotaPlanLimit[];
  createdAt?: string | null;
  updatedAt?: string | null;
};

export type FeatureQuotaPlanInput = {
  code: string;
  name: string;
  description: string;
  isActive: boolean;
  limits: FeatureQuotaPlanLimit[];
};

export type FeatureQuotaLimitSource =
  | 'user_override'
  | 'whitelist_feature'
  | 'whitelist_all'
  | 'plan'
  | 'global_policy';

export type FeatureQuotaEntitlement = {
  featureCode: string;
  name: string;
  description: string;
  dailyLimit?: number | null;
  usedCount: number;
  remaining?: number | null;
  unlimited: boolean;
  disabled: boolean;
  resetAt: string;
  limitSource: FeatureQuotaLimitSource;
  planCode?: string | null;
  effectiveUntil?: string | null;
};

export type FeatureQuotaPlanAssignment = {
  planCode: string;
  planName: string;
  effectiveFrom: string;
  effectiveUntil?: string | null;
};

export type FeatureQuotaUserOverride = {
  featureCode: string;
  dailyLimit: number;
  effectiveFrom: string;
  effectiveUntil?: string | null;
  reason: string;
};

export type FeatureQuotaWhitelistEntry = {
  featureCode?: string | null;
  effectiveFrom: string;
  effectiveUntil?: string | null;
  reason: string;
};

export type FeatureQuotaUserProfile = {
  userId: number;
  planAssignment?: FeatureQuotaPlanAssignment | null;
  planAssignments: FeatureQuotaPlanAssignment[];
  overrides: FeatureQuotaUserOverride[];
  whitelistEntries: FeatureQuotaWhitelistEntry[];
  entitlements: FeatureQuotaEntitlement[];
};

export type RbacCatalog = {
  permissions: RbacPermission[];
  roles: RbacRole[];
};

export type RbacRoleInput = {
  code: string;
  name: string;
  description: string;
  permissions: string[];
};

export const rbacAdminApi = {
  async getCatalog(): Promise<RbacCatalog> {
    const { data } = await apiClient.get('/api/v1/rbac/catalog');
    return toCamelCase<RbacCatalog>(data);
  },

  async listUsers(query: string, page: number, pageSize = 20): Promise<Paginated<RbacUser>> {
    const { data } = await apiClient.get('/api/v1/rbac/users', {
      params: { query: query || undefined, page, page_size: pageSize },
    });
    return toCamelCase<Paginated<RbacUser>>(data);
  },

  async replaceUserRoles(userId: number, roles: string[]): Promise<{ roles: string[]; permissions: string[] }> {
    const { data } = await apiClient.put(`/api/v1/rbac/users/${userId}/roles`, { roles });
    return toCamelCase<{ roles: string[]; permissions: string[] }>(data);
  },

  async setUserActive(userId: number, isActive: boolean): Promise<{ id: number; isActive: boolean }> {
    const { data } = await apiClient.patch(`/api/v1/rbac/users/${userId}/active`, {
      is_active: isActive,
    });
    return toCamelCase<{ id: number; isActive: boolean }>(data);
  },

  async createRole(input: RbacRoleInput): Promise<RbacRole> {
    const { data } = await apiClient.post('/api/v1/rbac/roles', input);
    return toCamelCase<RbacRole>(data);
  },

  async updateRole(roleCode: string, input: Omit<RbacRoleInput, 'code'>): Promise<RbacRole> {
    const { data } = await apiClient.put(`/api/v1/rbac/roles/${encodeURIComponent(roleCode)}`, input);
    return toCamelCase<RbacRole>(data);
  },

  async deleteRole(roleCode: string): Promise<void> {
    await apiClient.delete(`/api/v1/rbac/roles/${encodeURIComponent(roleCode)}`);
  },

  async listFeatureQuotaPolicies(): Promise<FeatureQuotaPolicy[]> {
    const { data } = await apiClient.get('/api/v1/rbac/feature-quotas');
    return toCamelCase<{ items: FeatureQuotaPolicy[] }>(data).items;
  },

  async updateFeatureQuotaPolicy(featureCode: string, dailyLimit: number): Promise<FeatureQuotaPolicy> {
    const { data } = await apiClient.put(
      `/api/v1/rbac/feature-quotas/${encodeURIComponent(featureCode)}`,
      { daily_limit: dailyLimit },
    );
    return toCamelCase<FeatureQuotaPolicy>(data);
  },

  async listFeatureQuotaPlans(): Promise<FeatureQuotaPlan[]> {
    const { data } = await apiClient.get('/api/v1/rbac/feature-quotas/plans');
    return toCamelCase<{ items: FeatureQuotaPlan[] }>(data).items;
  },

  async createFeatureQuotaPlan(input: FeatureQuotaPlanInput): Promise<FeatureQuotaPlan> {
    const { data } = await apiClient.post('/api/v1/rbac/feature-quotas/plans', {
      code: input.code,
      name: input.name,
      description: input.description,
      is_active: input.isActive,
      limits: input.limits.map((item) => ({ feature_code: item.featureCode, daily_limit: item.dailyLimit })),
    });
    return toCamelCase<FeatureQuotaPlan>(data);
  },

  async updateFeatureQuotaPlan(planCode: string, input: Omit<FeatureQuotaPlanInput, 'code'>): Promise<FeatureQuotaPlan> {
    const { data } = await apiClient.put(`/api/v1/rbac/feature-quotas/plans/${encodeURIComponent(planCode)}`, {
      name: input.name,
      description: input.description,
      is_active: input.isActive,
      limits: input.limits.map((item) => ({ feature_code: item.featureCode, daily_limit: item.dailyLimit })),
    });
    return toCamelCase<FeatureQuotaPlan>(data);
  },

  async getUserFeatureQuotaProfile(userId: number): Promise<FeatureQuotaUserProfile> {
    const { data } = await apiClient.get(`/api/v1/rbac/feature-quotas/users/${userId}/quota-profile`);
    return toCamelCase<FeatureQuotaUserProfile>(data);
  },

  async assignUserFeatureQuotaPlan(
    userId: number,
    planCode: string | null,
    effectiveUntil?: string | null,
  ): Promise<FeatureQuotaUserProfile> {
    const { data } = await apiClient.put(`/api/v1/rbac/feature-quotas/users/${userId}/plan`, {
      plan_code: planCode,
      effective_until: effectiveUntil || null,
    });
    return toCamelCase<FeatureQuotaUserProfile>(data);
  },

  async setUserFeatureQuotaOverride(
    userId: number,
    featureCode: string,
    dailyLimit: number,
    reason = '',
  ): Promise<FeatureQuotaUserProfile> {
    const { data } = await apiClient.put(
      `/api/v1/rbac/feature-quotas/users/${userId}/overrides/${encodeURIComponent(featureCode)}`,
      { daily_limit: dailyLimit, reason },
    );
    return toCamelCase<FeatureQuotaUserProfile>(data);
  },

  async revokeUserFeatureQuotaOverride(userId: number, featureCode: string): Promise<FeatureQuotaUserProfile> {
    const { data } = await apiClient.delete(
      `/api/v1/rbac/feature-quotas/users/${userId}/overrides/${encodeURIComponent(featureCode)}`,
    );
    return toCamelCase<FeatureQuotaUserProfile>(data);
  },

  async setUserFeatureQuotaWhitelist(
    userId: number,
    featureCode: string | null,
    reason = '',
  ): Promise<FeatureQuotaUserProfile> {
    const { data } = await apiClient.put(`/api/v1/rbac/feature-quotas/users/${userId}/whitelist`, {
      feature_code: featureCode,
      reason,
    });
    return toCamelCase<FeatureQuotaUserProfile>(data);
  },

  async revokeUserFeatureQuotaWhitelist(
    userId: number,
    featureCode: string | null,
  ): Promise<FeatureQuotaUserProfile> {
    const { data } = await apiClient.delete(`/api/v1/rbac/feature-quotas/users/${userId}/whitelist`, {
      params: { feature_code: featureCode ?? undefined },
    });
    return toCamelCase<FeatureQuotaUserProfile>(data);
  },

  async listAudit(page: number, pageSize = 20): Promise<Paginated<RbacAuditEvent>> {
    const { data } = await apiClient.get('/api/v1/rbac/audit', {
      params: { page, page_size: pageSize },
    });
    return toCamelCase<Paginated<RbacAuditEvent>>(data);
  },
};
