import { expect, test } from '@playwright/test';

const controlledOAuth = process.env.DSA_WEB_SMOKE_OAUTH === 'true';

test.skip(!controlledOAuth, 'Set DSA_WEB_SMOKE_OAUTH=true to run browser smoke tests.');

test.describe('Web OAuth smoke', () => {
  test.use({ locale: 'zh-CN' });

  test('login page presents WeChat QR-code sign-in only', async ({ page }) => {
    await page.goto('/login');
    await page.waitForLoadState('domcontentloaded');

    await expect(page.getByText('微信开放平台登录')).toBeVisible();
    await expect(page.getByRole('heading', { name: '使用微信扫码登录' })).toBeVisible();
    await expect(page.getByRole('button', { name: '前往微信扫码登录' })).toBeVisible();
    await expect(page.locator('#password')).toHaveCount(0);
  });

  test('login CTA initiates a top-level navigation to the OAuth start endpoint', async ({ page }) => {
    await page.goto('/login?redirect=%2Fsettings');
    await page.waitForLoadState('domcontentloaded');

    await page.route('**/api/v1/web-auth/wechat/start', (route) => route.abort('blockedbyclient'));
    const oauthStartRequest = page.waitForRequest((request) => (
      request.isNavigationRequest()
      && new URL(request.url()).pathname === '/api/v1/web-auth/wechat/start'
    ));

    await page.getByRole('button', { name: '前往微信扫码登录' }).click();

    const request = await oauthStartRequest;
    expect(request.method()).toBe('GET');
    expect(request.isNavigationRequest()).toBe(true);
  });

  test('controlled OAuth session exposes permitted workspace navigation', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    test.skip(/\/login(?:\?|$)/.test(new URL(page.url()).pathname), (
      'Provide DSA_WEB_SMOKE_OAUTH_STORAGE_STATE from a controlled real OAuth session for authenticated checks.'
    ));

    await expect(page.getByRole('link', { name: '首页' })).toBeVisible();
  });
});
