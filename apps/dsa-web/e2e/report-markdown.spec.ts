import { expect, test, type Page } from '@playwright/test';

const controlledOAuth = process.env.DSA_WEB_SMOKE_OAUTH === 'true';
const UI_LANGUAGE_STORAGE_KEY = 'dsa.uiLanguage';

test.skip(!controlledOAuth, 'Set DSA_WEB_SMOKE_OAUTH=true to run controlled OAuth browser tests.');
test.use({ locale: 'zh-CN' });

async function openAuthenticatedWorkspace(page: Page) {
  await page.addInitScript((storageKey) => {
    window.localStorage.setItem(storageKey, 'zh');
  }, UI_LANGUAGE_STORAGE_KEY);
  await page.goto('/');
  await page.waitForLoadState('domcontentloaded');

  test.skip(/\/login(?:\?|$)/.test(new URL(page.url()).pathname), (
    'Provide DSA_WEB_SMOKE_OAUTH_STORAGE_STATE from a controlled real OAuth session for authenticated checks.'
  ));

  await expect(page.getByPlaceholder('输入股票代码或名称，如 600519、贵州茅台、AAPL')).toBeVisible({ timeout: 10_000 });
}

test.describe('ReportMarkdown component', () => {
  test('copy markdown source code', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await openAuthenticatedWorkspace(page);

    await expect(page.getByText('历史分析')).toBeVisible({ timeout: 10_000 });
    const firstHistoryItem = page.locator('.home-history-item').first();
    await expect(firstHistoryItem).toBeVisible({ timeout: 10_000 });
    await firstHistoryItem.click();

    const detailedReportButton = page.getByRole('button', { name: '完整分析报告' });
    await expect(detailedReportButton).toBeEnabled({ timeout: 3_000 });
    await detailedReportButton.click();
    await expect(page.getByRole('dialog').getByText('完整分析报告')).toBeVisible();

    const copyMarkdownButton = page.getByRole('button', { name: '复制 Markdown 源码' });
    await copyMarkdownButton.click();
    const clipboardText = await page.evaluate(() => navigator.clipboard.readText());
    expect(clipboardText).toBeTruthy();
    expect(clipboardText.length).toBeGreaterThan(0);
  });

  test('copy plain text removes markdown structure', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await openAuthenticatedWorkspace(page);

    const firstHistoryItem = page.locator('.home-history-item').first();
    await expect(firstHistoryItem).toBeVisible({ timeout: 10_000 });
    await firstHistoryItem.click();
    await page.getByRole('button', { name: '完整分析报告' }).click();

    const copyPlainTextButton = page.getByRole('button', { name: '复制纯文本' });
    await expect(copyPlainTextButton).toBeVisible({ timeout: 5_000 });
    await copyPlainTextButton.click();

    const clipboardText = await page.evaluate(() => navigator.clipboard.readText());
    expect(clipboardText).toBeTruthy();
    expect(clipboardText).not.toMatch(/^#{1,6}\s+/m);
    expect(clipboardText).not.toMatch(/\*\*[^*]+\*\*/);
  });

  test('mobile report controls remain available', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openAuthenticatedWorkspace(page);

    const detailedReportButton = page.getByRole('button', { name: '完整分析报告' });
    await expect(detailedReportButton).toBeVisible({ timeout: 5_000 });
    await detailedReportButton.click();

    await expect(page.getByRole('button', { name: '复制 Markdown 源码' })).toBeVisible({ timeout: 5_000 });
    await expect(page.getByRole('button', { name: '复制纯文本' })).toBeVisible();
  });
});
