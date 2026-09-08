import type { AxiosResponse } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient, { FEATURE_QUOTA_CHANGED_EVENT, isSessionProbe } from '../index';

type ResponseInterceptor = {
  fulfilled?: (response: AxiosResponse) => AxiosResponse;
  rejected?: (error: unknown) => Promise<never>;
};

function runSuccessInterceptor(status: number, method: string): void {
  const handlers = (apiClient.interceptors.response as unknown as {
    handlers: ResponseInterceptor[];
  }).handlers;
  const handler = handlers.find((candidate) => candidate.fulfilled);
  if (!handler?.fulfilled) throw new Error('API success response interceptor is not registered');
  handler.fulfilled({ status, config: { method } } as AxiosResponse);
}

describe('apiClient feature quota refresh notifications', () => {
  const onQuotaChanged = vi.fn();

  beforeEach(() => {
    onQuotaChanged.mockReset();
    window.addEventListener(FEATURE_QUOTA_CHANGED_EVENT, onQuotaChanged);
  });

  afterEach(() => {
    window.removeEventListener(FEATURE_QUOTA_CHANGED_EVENT, onQuotaChanged);
  });

  it('notifies quota consumers after a successful unsafe request', () => {
    runSuccessInterceptor(202, 'post');

    expect(onQuotaChanged).toHaveBeenCalledOnce();
  });

  it('does not treat a validateStatus-accepted non-2xx response as a quota change', () => {
    runSuccessInterceptor(409, 'post');

    expect(onQuotaChanged).not.toHaveBeenCalled();
  });
});


describe('apiClient logout session handling', () => {
  it.each(['/api/v1/web-auth/me', '/api/v1/web-auth/logout'])(
    'treats %s as a single OAuth session probe',
    (url) => {
      expect(isSessionProbe(url)).toBe(true);
    },
  );

  it('does not recognize removed password-auth endpoints as session probes', () => {
    expect(isSessionProbe('/api/v1/auth/logout')).toBe(false);
  });
});
