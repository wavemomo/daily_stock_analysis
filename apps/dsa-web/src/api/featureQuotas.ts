import apiClient from './index';

export type FeatureQuota = {
  feature_code: string;
  daily_limit: number | null;
  remaining: number | null;
  unlimited: boolean;
  disabled: boolean;
};

export type FeatureQuotaResponse = { items: FeatureQuota[] };

export const featureQuotaApi = {
  async me(): Promise<FeatureQuotaResponse> {
    const { data } = await apiClient.get<FeatureQuotaResponse>('/api/v1/feature-quotas/me');
    return data;
  },
};
