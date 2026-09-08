import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FEATURE_QUOTA_CHANGED_EVENT } from '../../api';
import { FeatureQuotaProvider, useFeatureQuotas } from '../FeatureQuotaContext';

const actorState = vi.hoisted(() => ({ actor: 'web_user' as 'web_user' | 'anonymous' | 'admin' }));
const me = vi.hoisted(() => vi.fn());

vi.mock('../AuthContext', () => ({
  useAuth: () => actorState,
}));

vi.mock('../../api/featureQuotas', () => ({
  featureQuotaApi: { me },
}));

function QuotaProbe() {
  const { describe } = useFeatureQuotas();
  return <output>{describe()}</output>;
}

describe('FeatureQuotaProvider', () => {
  beforeEach(() => {
    actorState.actor = 'web_user';
    me.mockReset();
  });

  it('reloads authoritative remaining quotas after a successful quota-consuming mutation', async () => {
    me
      .mockResolvedValueOnce({
        items: [{ feature_code: 'stock_analysis', remaining: 5, unlimited: false, disabled: false }],
      })
      .mockResolvedValueOnce({
        items: [{ feature_code: 'stock_analysis', remaining: 4, unlimited: false, disabled: false }],
      });

    render(<FeatureQuotaProvider><QuotaProbe /></FeatureQuotaProvider>);

    expect(await screen.findByText('stock_analysis 5')).toBeInTheDocument();
    window.dispatchEvent(new CustomEvent(FEATURE_QUOTA_CHANGED_EVENT));

    await waitFor(() => expect(screen.getByText('stock_analysis 4')).toBeInTheDocument());
    expect(me).toHaveBeenCalledTimes(2);
  });

  it('also reloads after a feature_quota_exceeded response event', async () => {
    me
      .mockResolvedValueOnce({
        items: [{ feature_code: 'stock_analysis', remaining: 1, unlimited: false, disabled: false }],
      })
      .mockResolvedValueOnce({
        items: [{ feature_code: 'stock_analysis', remaining: 0, unlimited: false, disabled: false }],
      });

    render(<FeatureQuotaProvider><QuotaProbe /></FeatureQuotaProvider>);

    expect(await screen.findByText('stock_analysis 1')).toBeInTheDocument();
    window.dispatchEvent(new CustomEvent('dsa:feature-quota-exceeded'));

    await waitFor(() => expect(screen.getByText('stock_analysis 0')).toBeInTheDocument());
    expect(me).toHaveBeenCalledTimes(2);
  });

  it('shows a retryable error instead of presenting a failed initial load as empty quota data', async () => {
    me.mockRejectedValueOnce(new Error('network unavailable'));

    render(<FeatureQuotaProvider><QuotaProbe /></FeatureQuotaProvider>);

    expect(await screen.findByText('额度信息加载失败，请重试')).toBeInTheDocument();
    expect(screen.queryByText('暂无额度信息')).not.toBeInTheDocument();
  });

  it('retains the last known quota summary when a refresh fails', async () => {
    me
      .mockResolvedValueOnce({
        items: [{ feature_code: 'stock_analysis', remaining: 5, unlimited: false, disabled: false }],
      })
      .mockRejectedValueOnce(new Error('network unavailable'));

    render(<FeatureQuotaProvider><QuotaProbe /></FeatureQuotaProvider>);

    expect(await screen.findByText('stock_analysis 5')).toBeInTheDocument();
    window.dispatchEvent(new CustomEvent(FEATURE_QUOTA_CHANGED_EVENT));

    await waitFor(() => expect(screen.getByText('stock_analysis 5（更新失败）')).toBeInTheDocument());
    expect(screen.queryByText('暂无额度信息')).not.toBeInTheDocument();
  });
});
