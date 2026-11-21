import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { analysisDetail, analysisPage } from '@/test/analysis-api-fixtures';
import { overviewPositions, overviewScope } from '@/test/overview-api-fixtures';
import PortfolioApiView from './PortfolioApiView';

const orders = [
  { ...analysisDetail.order, executedQuantity: '0.0000000001' },
  {
    ...analysisDetail.order,
    id: '9700',
    status: 'LIVE_UNKNOWN',
    executedQuantity: null,
    executedFundsKrw: null,
  },
  ...Array.from({ length: 19 }, (_, i) => ({
    ...analysisDetail.order,
    id: String(9699 - i),
    tradingMode: 'MOCK',
    status: 'MOCK_EXECUTED',
  })),
];
const fills = Array.from({ length: 21 }, (_, index) => ({
  id: String(index + 1),
  quantity: '0.0000000001',
  price: '12345678901234567890.1234567890',
  fundsKrw: '1234567890.1234567890',
  recordedAt: '2026-02-03T20:12:00Z',
}));
const json = (body: unknown) =>
  new Response(JSON.stringify(body), {
    headers: { 'content-type': 'application/json' },
  });
function setup(override?: (url: URL) => Response | undefined) {
  const fetcher = vi.fn(async (path: string, options: RequestInit) => {
    expect(options).toMatchObject({
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      redirect: 'error',
    });
    const url = new URL(path, 'http://localhost');
    const replacement = override?.(url);
    if (replacement) return replacement;
    const page = Number(url.searchParams.get('page') ?? 0);
    if (url.pathname === '/api/v1/overview/positions') return json(overviewPositions);
    if (url.pathname === '/api/v1/portfolio/orders')
      return json({ ...analysisPage(orders, page), scope: overviewScope });
    if (url.pathname === '/api/v1/portfolio/orders/9701/fills')
      return json({ ...analysisPage(fills, page), scope: overviewScope, orderId: '9701' });
    throw new Error('Unrecognized endpoint');
  });
  vi.stubGlobal('fetch', fetcher);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PortfolioApiView />
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), fetcher };
}
afterEach(() => vi.unstubAllGlobals());

it('loads the selected tab, preserves partial valuations, and pages through scoped orders and fills', async () => {
  const { user, fetcher } = setup();
  await screen.findByRole('table', { name: 'API 보유 자산' });
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(screen.getByText('총 계좌 평가금액').nextElementSibling).toHaveTextContent('미기록');
  expect(screen.getByText('보유 현금 · KRW').nextElementSibling).toHaveTextContent('1,000원');
  await user.click(screen.getByRole('button', { name: '주문·체결 이력' }));
  const table = await screen.findByRole('table', { name: '봇 주문 이력' });
  const unknown = within(table).getByRole('row', { name: /확인 필요/ });
  expect(within(unknown).getAllByText('미기록')).toHaveLength(2);
  expect(unknown).toHaveTextContent('확인 필요');
  expect(table).toHaveTextContent('MOCK');
  expect(table).toHaveTextContent('0.0000000001');
  expect(fetcher).toHaveBeenCalledTimes(2);
  await user.click(within(table).getByRole('button', { name: '주문 9701 체결 상세' }));
  const drawer = within(screen.getByRole('dialog'));
  const detail = await drawer.findByRole('table', { name: '개별 체결 내역' });
  expect(detail).toHaveTextContent('원장 기록 시각');
  expect(detail).toHaveTextContent('2026.02.04 05:12 KST');
  expect(detail).toHaveTextContent('12,345,678,901,234,567,890.123456789');
  expect(detail).toHaveTextContent('1,234,567,890.123456789원');
  expect(drawer.queryByText(/Mock 데이터/)).not.toBeInTheDocument();
  await user.click(drawer.getByRole('button', { name: '다음 체결 페이지' }));
  expect(await drawer.findByRole('table', { name: '개별 체결 내역' })).toHaveTextContent(
    '0.0000000001',
  );
  expect(fetcher.mock.calls.at(-1)?.[0]).toBe('/api/v1/portfolio/orders/9701/fills?page=1&size=20');
  await user.keyboard('{Escape}');
  await user.click(screen.getByRole('button', { name: '다음 주문 페이지' }));
  expect(await screen.findByRole('button', { name: '주문 9681 체결 상세' })).toBeInTheDocument();
  expect(fetcher.mock.calls.at(-1)?.[0]).toBe('/api/v1/portfolio/orders?page=1&size=20');
});

it.each([
  { orderId: '9700', scope: overviewScope },
  { orderId: '9701', scope: { ...overviewScope, userId: '2' } },
])('hides a fill response for another order or scope', async (mismatch) => {
  const { user } = setup((url) =>
    url.pathname.endsWith('/fills') ? json({ ...analysisPage(fills), ...mismatch }) : undefined,
  );
  await screen.findByRole('table', { name: 'API 보유 자산' });
  await user.click(screen.getByRole('button', { name: '주문·체결 이력' }));
  await user.click(await screen.findByRole('button', { name: '주문 9701 체결 상세' }));
  const drawer = within(screen.getByRole('dialog'));
  expect(await drawer.findByRole('alert')).toHaveTextContent('조회 기준이 변경');
  expect(drawer.queryByRole('table')).not.toBeInTheDocument();
});

it('distinguishes a failed history read from an empty history and can retry', async () => {
  let available = false;
  const { user } = setup((url) =>
    url.pathname.endsWith('/orders')
      ? available
        ? json({ ...analysisPage([]), scope: overviewScope })
        : new Response('private internal error', { status: 503 })
      : undefined,
  );
  await screen.findByRole('table', { name: 'API 보유 자산' });
  await user.click(screen.getByRole('button', { name: '주문·체결 이력' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('서버 오류');
  expect(screen.queryByText('private internal error')).not.toBeInTheDocument();
  expect(screen.queryByText('저장된 봇 주문이 없습니다')).not.toBeInTheDocument();
  available = true;
  await user.click(screen.getByRole('button', { name: '다시 시도' }));
  expect(await screen.findByText('저장된 봇 주문이 없습니다')).toBeInTheDocument();
});
