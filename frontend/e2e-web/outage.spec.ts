import { expect, test } from '@playwright/test';

test('backend outage stays an error and does not silently become DEMO', async ({
  page,
  request,
}) => {
  const response = await request.get('/api/v1/auth/me');
  expect([502, 504]).toContain(response.status());
  expect(response.headers()['cache-control']).toBe('no-store');
  await page.goto('/overview');
  await expect(page.getByRole('alert')).toContainText('인증 상태를 확인하지 못했습니다', {
    timeout: 15000,
  });
  await expect(page.getByText('DEMO', { exact: true })).toHaveCount(0);
});
