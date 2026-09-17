import { expect, test } from '@playwright/test';

const runSmoke = process.env.DSA_WEB_SMOKE === 'true';

test.skip(!runSmoke, 'Set DSA_WEB_SMOKE=true to run browser smoke tests.');

test.describe('Web login smoke', () => {
  test.use({ locale: 'zh-CN' });

  test('login page presents the email/password sign-in form', async ({ page }) => {
    await page.goto('/login');
    await page.waitForLoadState('domcontentloaded');

    await expect(page.getByRole('heading', { name: '登录主升浪 Web 端' })).toBeVisible();
    await expect(page.getByPlaceholder('you@example.com')).toBeVisible();
    await expect(page.getByPlaceholder('请输入密码')).toBeVisible();
    await expect(page.getByRole('button', { name: '登录' })).toBeVisible();
    await expect(page.getByRole('button', { name: '前往微信扫码登录' })).toHaveCount(0);
  });

  test('authenticated session exposes permitted workspace navigation', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    test.skip(/\/login(?:\?|$)/.test(new URL(page.url()).pathname), (
      'Provide DSA_WEB_SMOKE_STORAGE_STATE from a controlled logged-in session for authenticated checks.'
    ));

    await expect(page.getByRole('link', { name: '首页' })).toBeVisible();
  });
});
