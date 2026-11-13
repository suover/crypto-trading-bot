import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '@/app/App';
import { DemoScenarioProvider } from '@/hooks/DemoScenarioProvider';
import { createMockRepository } from '@/services/mock-dashboard-repository';
import { TooltipProvider } from '@/components/ui/tooltip';
import {
  overviewFixtureResponse,
  overviewSummary,
  overviewPositions,
  performanceRow,
  overviewBigId,
} from '@/test/overview-api-fixtures';
import { overviewApiAvailable } from '@/hooks/use-overview-source';
const requests: string[] = [];
const json = (data: unknown) =>
  new Response(JSON.stringify(data), { headers: { 'content-type': 'application/json' } });
beforeEach(() => {
  requests.length = 0;
  vi.stubEnv('VITE_ENABLE_OVERVIEW_API', 'true');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      requests.push(input);
      return new Response(overviewFixtureResponse(new URL(input, 'http://local')), {
        headers: { 'content-type': 'application/json' },
      });
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
function setup(scenario: 'normal' | 'error' | 'loading' = 'normal') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = render(
    <QueryClientProvider client={client}>
      <DemoScenarioProvider initialScenario={scenario} adapter={createMockRepository(0)}>
        <TooltipProvider>
          <MemoryRouter initialEntries={['/overview']}>
            <App />
          </MemoryRouter>
        </TooltipProvider>
      </DemoScenarioProvider>
    </QueryClientProvider>,
  );
  return { ...ui, client, user: userEvent.setup() };
}
async function enter(user: ReturnType<typeof userEvent.setup>) {
  // The first route render includes lazy module loading, especially on a cold test run.
  await user.click(
    await screen.findByRole('button', { name: 'LOCAL API · READ ONLY' }, { timeout: 5000 }),
  );
}
describe('Overview API view', () => {
  it('cancels in-flight API requests when switching back to DEMO', async () => {
    const signals: AbortSignal[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_input: string, init: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            const signal = init.signal!;
            signals.push(signal);
            signal.addEventListener('abort', () =>
              reject(new DOMException('Aborted', 'AbortError')),
            );
          }),
      ),
    );
    const { user } = setup();
    await enter(user);
    await waitFor(() => expect(signals).toHaveLength(3));
    await user.click(screen.getByRole('button', { name: 'DEMO 화면' }));
    await waitFor(() => expect(signals.every((s) => s.aborted)).toBe(true));
    expect(
      await screen.findByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' }),
    ).toBeInTheDocument();
  });
  it('keeps DEMO as default and never fetches without explicit selection', async () => {
    setup();
    await screen.findByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' }, { timeout: 5000 });
    expect(requests).toEqual([]);
    expect(overviewApiAvailable(false, 'true')).toBe(false);
    expect(overviewApiAvailable(true, 'false')).toBe(false);
  });
  it('hides the switch without opt-in', async () => {
    vi.stubEnv('VITE_ENABLE_OVERVIEW_API', '');
    setup();
    await screen.findByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' });
    expect(screen.queryByRole('group', { name: '대시보드 데이터 출처' })).not.toBeInTheDocument();
    expect(requests).toEqual([]);
  });
  it('renders partial values, exact decimals, small quantities and no fabricated cards or detail links', async () => {
    const { user } = setup();
    await enter(user);
    await screen.findByRole('table', { name: 'API 보유 자산' });
    expect(screen.getByText('12,345,678,901,234,567,890.123456789원')).toBeInTheDocument();
    expect(screen.getByText('-12,345.6789원')).toHaveClass('negative');
    expect(screen.getAllByText('PARTIAL').length).toBeGreaterThan(0);
    expect(screen.getAllByText('0.0000000001')).toHaveLength(3);
    expect(screen.getByText('평가 불가')).toBeInTheDocument();
    expect(screen.getByText('계산 대상 제외')).toBeInTheDocument();
    expect(screen.queryByLabelText('데모 데이터 상태')).not.toBeInTheDocument();
    expect(screen.queryByText('전일 대비 · 입출금 포함')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '현재 API 미제공' })).toBeInTheDocument();
    expect(document.querySelector('a[href*="analysis="]')).toBeNull();
    expect(screen.getByRole('table', { name: 'API 최근 추천' })).toHaveTextContent('APPROVED');
  });
  it.each(['error', 'loading'] as const)('does not depend on global DEMO %s', async (scenario) => {
    const { user } = setup(scenario);
    await enter(user);
    expect(await screen.findByRole('table', { name: 'API 보유 자산' })).toBeInTheDocument();
    expect(screen.queryByText('데모 데이터를 불러오지 못했습니다')).not.toBeInTheDocument();
  });
  it('distinguishes no snapshot, empty positions, baseline and actual zero', async () => {
    const summary = {
      ...overviewSummary,
      portfolio: {
        ...overviewSummary.portfolio!,
        valuationStatus: 'COMPLETE',
        totalValueKrw: '0.0000000000',
      },
      performance: { ...performanceRow(1, 'BASELINE'), portfolioSnapshotId: overviewBigId },
      botPnl: null,
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string) =>
        input.endsWith('/summary')
          ? json(summary)
          : input.endsWith('/positions')
            ? json({ ...overviewPositions, items: [] })
            : new Response(overviewFixtureResponse(new URL(input, 'http://local')), {
                headers: { 'content-type': 'application/json' },
              }),
      ),
    );
    const { user } = setup();
    await enter(user);
    expect(
      await screen.findByText('이 snapshot에 저장된 보유 자산이 없습니다'),
    ).toBeInTheDocument();
    expect(screen.getByText('0원')).toBeInTheDocument();
    expect(screen.getByText(/새 기준점 · 검증된 성과 아님/)).toBeInTheDocument();
    expect(screen.getByText('회계 요약 미기록')).toBeInTheDocument();
  });
  it('leaves a genuinely absent snapshot as unrecorded', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string) =>
        input.endsWith('/summary')
          ? json({ ...overviewSummary, portfolio: null, performance: null, botPnl: null })
          : input.endsWith('/positions')
            ? json({ ...overviewPositions, portfolio: null, items: [] })
            : new Response(overviewFixtureResponse(new URL(input, 'http://local')), {
                headers: { 'content-type': 'application/json' },
              }),
      ),
    );
    const { user } = setup();
    await enter(user);
    await screen.findByText('계좌 snapshot이 없습니다');
    expect(screen.queryByText('0원')).not.toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'API 보유 자산' })).not.toBeInTheDocument();
  });
  it.each([401, 403, 500])(
    'shows %i without retry, sensitive body or DEMO fallback',
    async (status) => {
      const fetcher = vi.fn(async () => new Response('private-password', { status }));
      vi.stubGlobal('fetch', fetcher);
      const { user } = setup();
      await enter(user);
      await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(3));
      expect(fetcher).toHaveBeenCalledTimes(3);
      expect(screen.queryByText('private-password')).not.toBeInTheDocument();
      expect(screen.queryByRole('table')).not.toBeInTheDocument();
      if (status === 401)
        expect(screen.getAllByRole('link', { name: '새 탭에서 관리자 인증' })[0]).toHaveAttribute(
          'href',
          '/api/v1/overview/summary',
        );
    },
  );
  it('keeps independent sections on failure and hides stale success after refresh failure', async () => {
    const { user } = setup();
    await enter(user);
    await screen.findByRole('table', { name: 'API 보유 자산' });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string) =>
        input.endsWith('/summary')
          ? new Response('', { status: 500 })
          : new Response(overviewFixtureResponse(new URL(input, 'http://local')), {
              headers: { 'content-type': 'application/json' },
            }),
      ),
    );
    await user.click(screen.getByRole('button', { name: 'API 새로고침' }));
    await screen.findByRole('alert');
    expect(screen.queryByRole('table', { name: 'API 보유 자산' })).not.toBeInTheDocument();
    expect(screen.queryByText('12,345,678,901,234,567,890.123456789원')).not.toBeInTheDocument();
    expect(await screen.findByRole('table', { name: 'API 최근 추천' })).toBeInTheDocument();
  });
  it('does not display mismatched positions, retries once, and recovers through refresh', async () => {
    let mismatch = true,
      count = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string) => {
        if (input.endsWith('/positions')) {
          count++;
          return json(
            mismatch
              ? {
                  ...overviewPositions,
                  portfolio: { ...overviewPositions.portfolio, snapshotId: '2' },
                  items: [],
                }
              : overviewPositions,
          );
        }
        return new Response(overviewFixtureResponse(new URL(input, 'http://local')), {
          headers: { 'content-type': 'application/json' },
        });
      }),
    );
    const { user } = setup();
    await enter(user);
    await screen.findByText(/조회 기준이 변경되었습니다/);
    expect(count).toBe(2);
    expect(screen.queryByRole('table', { name: 'API 보유 자산' })).not.toBeInTheDocument();
    mismatch = false;
    await user.click(screen.getByRole('button', { name: '다시 시도' }));
    expect(await screen.findByRole('table', { name: 'API 보유 자산' })).toBeInTheDocument();
  });
  it('uses server paging and explicit KST filters without relabelling row counts as days', async () => {
    const { user } = setup();
    await enter(user);
    await screen.findByRole('table', { name: 'API 성과 이력' });
    await user.click(screen.getByRole('button', { name: '다음 성과 페이지' }));
    await waitFor(() => expect(requests.some((u) => u.includes('performance?page=1'))).toBe(true));
    await user.type(screen.getByLabelText('성과 기간 시작 · KST'), '2026-01-01T09:00');
    await user.type(screen.getByLabelText('성과 기간 종료 · KST'), '2026-01-03T09:00');
    await user.click(screen.getByRole('button', { name: '기간 적용' }));
    await waitFor(() => expect(requests.at(-1)).toContain('from=2026-01-01T00%3A00%3A00.000Z'));
    expect(requests.at(-1)).toContain('page=0');
    await user.click(screen.getByRole('button', { name: '다음 추천 페이지' }));
    await waitFor(() => expect(requests.at(-1)).toContain('recommendations?page=1'));
    await user.selectOptions(screen.getByLabelText('추천 action'), 'HOLD');
    await waitFor(() => expect(requests.at(-1)).toContain('action=HOLD'));
    expect(requests.at(-1)).toContain('page=0');
    await waitFor(() =>
      expect(
        within(screen.getByRole('table', { name: 'API 최근 추천' })).queryByText('BUY'),
      ).not.toBeInTheDocument(),
    );
  });
  it('clears API queries on DEMO switch and labels navigation to other DEMO screens', async () => {
    const { user, client } = setup();
    await enter(user);
    await screen.findByRole('table', { name: 'API 보유 자산' });
    await user.click(screen.getByRole('button', { name: 'DEMO 화면' }));
    await screen.findByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' });
    await waitFor(() =>
      expect(client.getQueryCache().findAll({ queryKey: ['overview-local-api'] })).toHaveLength(0),
    );
    await user.click(screen.getByRole('link', { name: '자산 및 거래' }));
    expect(await screen.findByText('DEMO', { exact: true })).toBeInTheDocument();
  });
});
