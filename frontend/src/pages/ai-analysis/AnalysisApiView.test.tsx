// These view regressions assume an existing server session. Auth flows have separate tests.
vi.mock('@/services/auth-api', async (original) => ({
  ...(await original<typeof import('@/services/auth-api')>()),
  currentAdmin: vi.fn(async () => 'fixture-admin'),
}));
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useNavigate, type NavigateFunction } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '@/app/App';
import { DemoScenarioProvider } from '@/hooks/DemoScenarioProvider';
import { createMockRepository } from '@/services/mock-dashboard-repository';
import { TooltipProvider } from '@/components/ui/tooltip';
import {
  analysisFixtureResponse,
  analysisBigId,
  analysisDetail,
  analysisCandidate,
  analysisPage,
  analysisUniverse,
} from '@/test/analysis-api-fixtures';
import { overviewFixtureResponse, overviewScope } from '@/test/overview-api-fixtures';
import { analysisApiAvailable } from '@/hooks/use-analysis-source';
const requests: string[] = [];
const json = (value: unknown) =>
  new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } });
beforeEach(() => {
  requests.length = 0;
  vi.stubEnv('VITE_ENABLE_AI_ANALYSIS_API', 'true');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      requests.push(input);
      const url = new URL(input, 'http://local');
      return new Response(
        url.pathname.startsWith('/api/v1/overview/')
          ? overviewFixtureResponse(url)
          : analysisFixtureResponse(url),
        { headers: { 'content-type': 'application/json' } },
      );
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
function setup(path = '/ai-analysis') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let navigate: NavigateFunction;
  function Navigation() {
    navigate = useNavigate();
    return <App />;
  }
  render(
    <QueryClientProvider client={client}>
      <DemoScenarioProvider adapter={createMockRepository(0)}>
        <TooltipProvider>
          <MemoryRouter initialEntries={[path]}>
            <Navigation />
          </MemoryRouter>
        </TooltipProvider>
      </DemoScenarioProvider>
    </QueryClientProvider>,
  );
  return {
    user: userEvent.setup(),
    navigate: (path: string) =>
      act(() => {
        void navigate(path);
      }),
  };
}
async function enter(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    await screen.findByRole('button', { name: 'LOCAL API · READ ONLY' }, { timeout: 5000 }),
  );
}
describe('AI Analysis local read view', () => {
  it('keeps DEMO default and disables opt-in outside development', async () => {
    setup();
    await screen.findByRole('heading', { name: '판단의 근거를, 한눈에' });
    expect(requests).toEqual([]);
    expect(analysisApiAvailable(false, 'true')).toBe(false);
    expect(analysisApiAvailable(true, '')).toBe(false);
  });
  it('keeps real-ID deep links out of DEMO and requires explicit opt-in', async () => {
    const { user } = setup(`/ai-analysis?recommendation=${analysisBigId}`);
    await screen.findByText(/실제 추천 상세 링크입니다/);
    expect(requests).toEqual([]);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await enter(user);
    expect(await screen.findByText('Application safety checked reason')).toBeInTheDocument();
    expect(
      within(screen.getByRole('dialog')).queryByText('DETAIL VIEW · DEMO'),
    ).not.toBeInTheDocument();
  });
  it('shows precise stored values, held candidates and separate execution without write buttons', async () => {
    const { user } = setup();
    await enter(user);
    await user.click(await screen.findByRole('button', { name: `${analysisBigId} 추천 상세` }));
    const dialog = screen.getByRole('dialog');
    expect(
      await within(dialog).findByText('실거래 체결 확인 (미체결 잔량 취소)'),
    ).toBeInTheDocument();
    expect(await within(dialog).findByRole('table', { name: 'API 당시 후보' })).toBeInTheDocument();
    expect(within(dialog).getAllByText('71.1234567890123456789012345678').length).toBeGreaterThan(
      0,
    );
    expect(within(dialog).getByText('HELD')).toBeInTheDocument();
    expect(within(dialog).getByText('불가 / 가능')).toBeInTheDocument();
    expect(within(dialog).getByText(/추천 수량 0.0000000001/)).toBeInTheDocument();
    expect(
      within(dialog).queryByRole('button', { name: /주문 실행|승인하기|재시도 실행/ }),
    ).not.toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: `${analysisBigId} 추천 상세` })).toHaveFocus();
  });
  it('uses server pages and filters, with no per-row detail requests', async () => {
    const { user } = setup();
    await enter(user);
    await screen.findByRole('table', { name: 'API AI 추천' });
    expect(requests).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: '다음 추천 페이지' }));
    await waitFor(() => expect(requests.at(-1)).toContain('page=1'));
    await user.selectOptions(screen.getByLabelText('추천 판단'), 'HOLD');
    await user.type(screen.getByLabelText('추천 마켓'), 'krw-btc');
    await user.click(screen.getByRole('button', { name: '조건 적용' }));
    await waitFor(() =>
      expect(requests.at(-1)).toContain('page=0&size=20&market=KRW-BTC&action=HOLD'),
    );
    expect(screen.queryByLabelText('승인 상태 필터')).not.toBeInTheDocument();
  });
  it.each([401, 403, 404, 500])(
    'shows HTTP %s without fake fallback or raw errors',
    async (status) => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('private SQL', { status })));
      const { user } = setup();
      await enter(user);
      if (status === 401) {
        expect(await screen.findByRole('heading', { name: '관리자 로그인' })).toBeInTheDocument();
        expect(screen.queryByRole('table')).not.toBeInTheDocument();
        return;
      }
      expect(await screen.findByRole('alert')).toBeInTheDocument();
      expect(screen.queryByRole('table')).not.toBeInTheDocument();
      expect(screen.queryByText('private SQL')).not.toBeInTheDocument();
    },
  );
  it('does not fetch candidates for HOLD without a verified universe', async () => {
    const { user } = setup('/ai-analysis?recommendation=2');
    await enter(user);
    await screen.findByText('HOLD는 정상적인 관망 판단입니다. 승인 요청이 필요하지 않습니다.');
    expect(screen.getByText(/당시 Universe 참조가 기록되지/)).toBeInTheDocument();
    expect(requests.some((r) => r.includes('/candidates'))).toBe(false);
  });
  it('renders reason as text, preserves UNKNOWN and rejects malformed data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string) =>
        input.includes('?page=')
          ? json({})
          : json({
              ...analysisDetail,
              finalDecisionReason: '<img src=x onerror=alert(1)>',
              universe: {
                status: 'INVALID_REFERENCE',
                analysisRunId: null,
                selectedCandidateId: null,
              },
              selectedCandidate: null,
              order: {
                ...analysisDetail.order,
                status: 'LIVE_UNKNOWN',
                executedFundsKrw: null,
                executedQuantity: null,
              },
            }),
      ),
    );
    const { user } = setup(`/ai-analysis?recommendation=${analysisBigId}`);
    await enter(user);
    expect(await screen.findByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(screen.getByText(/실거래 결과 미확정/)).toBeInTheDocument();
    expect(screen.getByText(/당시 Universe 연결 검증에 실패/)).toBeInTheDocument();
    expect(document.querySelector('img[src="x"]')).toBeNull();
  });
  it('cancels pending detail on navigation and never mixes earlier responses', async () => {
    let oldSignal: AbortSignal | undefined;
    let finish: ((v: Response) => void) | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string, init: RequestInit) => {
        if (input.endsWith('/' + analysisBigId)) {
          oldSignal = init.signal as AbortSignal;
          return new Promise<Response>((resolve) => {
            finish = resolve;
          });
        }
        return new Response(analysisFixtureResponse(new URL(input, 'http://local')), {
          headers: { 'content-type': 'application/json' },
        });
      }),
    );
    const { user, navigate } = setup(`/ai-analysis?recommendation=${analysisBigId}`);
    await enter(user);
    await waitFor(() => expect(oldSignal).toBeDefined());
    navigate('/ai-analysis?recommendation=2');
    await screen.findByText('HOLD는 정상적인 관망 판단입니다. 승인 요청이 필요하지 않습니다.');
    expect(oldSignal?.aborted).toBe(true);
    await act(async () => {
      finish?.(json({ ...analysisDetail, finalDecisionReason: 'OLD PRIVATE REASON' }));
    });
    expect(screen.queryByText('OLD PRIVATE REASON')).not.toBeInTheDocument();
  });
  it('cancels list when switching to DEMO and suppresses mock loading in API mode', async () => {
    let signal: AbortSignal | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((_input: string, init: RequestInit) => {
        signal = init.signal as AbortSignal;
        return new Promise(() => {});
      }),
    );
    const { user } = setup();
    await enter(user);
    await waitFor(() => expect(signal).toBeDefined());
    await user.click(screen.getByRole('button', { name: 'DEMO 화면' }));
    expect(signal?.aborted).toBe(true);
    await screen.findByRole('heading', { name: '판단의 근거를, 한눈에' });
  });
  it('links Overview recommendations to API detail, never a demo analysis ID', async () => {
    vi.stubEnv('VITE_ENABLE_OVERVIEW_API', 'true');
    const { user } = setup('/overview');
    await enter(user);
    const link = (await screen.findAllByRole('link', { name: /실제 추천 상세/ }))[0]!;
    expect(link.getAttribute('href')).toContain('?recommendation=');
    await user.click(link);
    // The fixtures deliberately have different scopes: cross-scope data is rejected.
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent(
      '조회 데이터 연결이 일치하지 않습니다',
    );
    expect(screen.queryByText('DETAIL VIEW · DEMO')).not.toBeInTheDocument();
  });
  it('opens the actual recommendation from Overview when scopes match', async () => {
    vi.stubEnv('VITE_ENABLE_OVERVIEW_API', 'true');
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string) => {
        requests.push(input);
        const url = new URL(input, 'http://local');
        if (url.pathname.startsWith('/api/v1/overview/'))
          return new Response(overviewFixtureResponse(url), {
            headers: { 'content-type': 'application/json' },
          });
        return json({ ...JSON.parse(analysisFixtureResponse(url)), scope: overviewScope });
      }),
    );
    const { user } = setup('/overview');
    await enter(user);
    const link = (await screen.findAllByRole('link', { name: /실제 추천 상세/ }))[0]!;
    const id = new URL(link.getAttribute('href')!, 'http://local').searchParams.get(
      'recommendation',
    );
    await user.click(link);
    const dialog = within(screen.getByRole('dialog'));
    expect(await dialog.findByText('Application safety checked reason')).toBeInTheDocument();
    expect(dialog.getByText(new RegExp(`추천 ${id} · 분석 실행`))).toBeInTheDocument();
    expect(dialog.queryByRole('alert')).not.toBeInTheDocument();
    expect(dialog.queryByText('DETAIL VIEW · DEMO')).not.toBeInTheDocument();
    expect(requests).toContain(`/api/v1/ai-trade-analysis/recommendations/${id}`);
  });
  it('pages through the same historical universe without fetching details for each candidate', async () => {
    const candidates = Array.from({ length: 21 }, (_, index) => ({
      ...analysisCandidate,
      id: String(8001 + index),
      market: index === 0 ? 'KRW-BTC' : `KRW-TEST${index}`,
      baseAsset: index === 0 ? 'BTC' : `TEST${index}`,
      rank: index + 1,
    }));
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string) => {
        requests.push(input);
        const url = new URL(input, 'http://local');
        if (url.pathname.endsWith('/candidates'))
          return json({
            ...analysisPage(candidates, Number(url.searchParams.get('page'))),
            universe: analysisUniverse,
          });
        return new Response(analysisFixtureResponse(url), {
          headers: { 'content-type': 'application/json' },
        });
      }),
    );
    const { user } = setup(`/ai-analysis?recommendation=${analysisBigId}`);
    await enter(user);
    const firstPage = await screen.findByRole('table', { name: 'API 당시 후보' });
    expect(within(firstPage).getAllByRole('row')).toHaveLength(21);
    await user.click(screen.getByRole('button', { name: '다음 후보 페이지' }));
    const lastPage = await screen.findByRole('table', { name: 'API 당시 후보' });
    expect(within(lastPage).getAllByRole('row')).toHaveLength(2);
    expect(within(lastPage).getByText('KRW-TEST20')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '다음 후보 페이지' })).toBeDisabled();
    expect(requests.at(-1)).toBe(
      `/api/v1/ai-trade-analysis/recommendations/${analysisBigId}/candidates?page=1&size=20`,
    );
    await user.click(screen.getByRole('button', { name: '이전 후보 페이지' }));
    const previousPage = await screen.findByRole('table', { name: 'API 당시 후보' });
    expect(within(previousPage).getAllByRole('row')).toHaveLength(21);
    expect(requests.filter((r) => r.endsWith(`/${analysisBigId}`))).toHaveLength(1);
  });
  it('hides API switch when the feature flag is disabled', async () => {
    vi.stubEnv('VITE_ENABLE_AI_ANALYSIS_API', '');
    setup(`/ai-analysis?recommendation=${analysisBigId}`);
    await screen.findByText(/로컬 API 연결이 활성화되지/);
    expect(screen.queryByRole('group', { name: 'AI 분석 데이터 출처' })).not.toBeInTheDocument();
    expect(requests).toEqual([]);
  });
});
