import type React from 'react';
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { FEATURE_QUOTA_CHANGED_EVENT } from '../api';
import { featureQuotaApi, type FeatureQuota } from '../api/featureQuotas';
import { useAuth } from './AuthContext';

type FeatureQuotaContextValue = {
  items: FeatureQuota[];
  isLoading: boolean;
  hasLoadError: boolean;
  refresh: () => Promise<void>;
  describe: () => string;
};

const FeatureQuotaContext = createContext<FeatureQuotaContextValue | null>(null);

function describeQuota(items: FeatureQuota[]): string {
  if (!items.length) return '暂无额度信息';
  const limited = items.filter((item) => !item.unlimited && !item.disabled && item.remaining !== null);
  if (!limited.length) return items.some((item) => item.unlimited) ? '额度不限' : '功能已停用';
  return limited.map((item) => `${item.feature_code} ${item.remaining}`).join(' · ');
}

export function FeatureQuotaProvider({ children }: { children: React.ReactNode }) {
  const { actor } = useAuth();
  const [items, setItems] = useState<FeatureQuota[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [hasLoadError, setHasLoadError] = useState(false);

  const refresh = useCallback(async () => {
    if (actor !== 'web_user') {
      setItems([]);
      setHasLoadError(false);
      return;
    }
    setIsLoading(true);
    setHasLoadError(false);
    try {
      const response = await featureQuotaApi.me();
      setItems(response.items ?? []);
    } catch {
      setHasLoadError(true);
    } finally {
      setIsLoading(false);
    }
  }, [actor]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const handleQuotaRefresh = () => void refresh();
    window.addEventListener('dsa:feature-quota-exceeded', handleQuotaRefresh);
    window.addEventListener(FEATURE_QUOTA_CHANGED_EVENT, handleQuotaRefresh);
    return () => {
      window.removeEventListener('dsa:feature-quota-exceeded', handleQuotaRefresh);
      window.removeEventListener(FEATURE_QUOTA_CHANGED_EVENT, handleQuotaRefresh);
    };
  }, [refresh]);

  const value = useMemo(() => ({
    items,
    isLoading,
    hasLoadError,
    refresh,
    describe: () => {
      // 服务端额度结果已包含 unlimited，不能把 RBAC 角色混入 Web 认证类型。
      const summary = describeQuota(items);
      if (!hasLoadError) return summary;
      return items.length ? `${summary}（更新失败）` : '额度信息加载失败，请重试';
    },
  }), [hasLoadError, isLoading, items, refresh]);

  return <FeatureQuotaContext.Provider value={value}>{children}</FeatureQuotaContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components -- Hook is intentionally co-located with its context provider.
export function useFeatureQuotas(): FeatureQuotaContextValue {
  const context = useContext(FeatureQuotaContext);
  if (!context) throw new Error('useFeatureQuotas must be used within FeatureQuotaProvider');
  return context;
}
