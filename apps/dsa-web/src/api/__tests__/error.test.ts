import { describe, expect, it } from 'vitest';
import { parseApiError } from '../error';

type QuotaPayloadShape = 'root' | 'detail';

function quotaError(reason: string | undefined, shape: QuotaPayloadShape) {
  const payload = {
    error: 'feature_quota_exceeded',
    message: '功能请求被拒绝',
    ...(reason === undefined ? {} : { reason }),
  };

  return {
    response: {
      status: 429,
      data: shape === 'detail' ? { detail: payload } : payload,
    },
  };
}

describe('parseApiError feature quota reasons', () => {
  it.each<QuotaPayloadShape>(['root', 'detail'])(
    'shows an administrator-disabled message for feature_disabled %s payloads',
    (shape) => {
      expect(parseApiError(quotaError('feature_disabled', shape))).toMatchObject({
        title: '该功能已被管理员停用',
        message: '当前功能暂不可用，请联系管理员恢复后再试。',
        rawMessage: '功能请求被拒绝',
        status: 429,
        category: 'feature_quota_exceeded',
      });
    },
  );

  it.each<QuotaPayloadShape>(['root', 'detail'])(
    'keeps the daily limit message for daily_limit_exceeded %s payloads',
    (shape) => {
      expect(parseApiError(quotaError('daily_limit_exceeded', shape))).toMatchObject({
        title: '今日功能额度已用尽',
        message: '该功能今日可用次数已耗尽，请明日再试或联系管理员调整额度。',
        status: 429,
        category: 'feature_quota_exceeded',
      });
    },
  );

  it.each<QuotaPayloadShape>(['root', 'detail'])(
    'uses the generic unavailable message for an unknown %s quota reason',
    (shape) => {
      expect(parseApiError(quotaError('unexpected_reason', shape))).toMatchObject({
        title: '功能暂不可用',
        message: '当前功能暂时不可用或请求受到限制，请稍后重试或联系管理员。',
        status: 429,
        category: 'feature_quota_exceeded',
      });
    },
  );

  it.each<QuotaPayloadShape>(['root', 'detail'])(
    'uses the generic unavailable message when the %s quota reason is missing',
    (shape) => {
      expect(parseApiError(quotaError(undefined, shape))).toMatchObject({
        title: '功能暂不可用',
        message: '当前功能暂时不可用或请求受到限制，请稍后重试或联系管理员。',
        status: 429,
        category: 'feature_quota_exceeded',
      });
    },
  );
});
