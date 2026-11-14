// These view regressions assume an existing server session. Auth flows have separate tests.
vi.mock('@/services/auth-api', async (original) => ({
  ...(await original<typeof import('@/services/auth-api')>()),
  currentAdmin: vi.fn(async () => 'fixture-admin'),
}));
import { act, render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '@/app/App';
import { DemoScenarioProvider } from '@/hooks/DemoScenarioProvider';
import { createMockRepository } from '@/services/mock-dashboard-repository';
import {
  fixtureResponse,
  bigId,
  candidateBody,
  pageBody,
  emptyAutomationBody,
} from '@/test/research-api-fixtures';
import { researchApiAvailable } from '@/hooks/use-research-source';
import { TooltipProvider } from '@/components/ui/tooltip';

const requests: string[] = [];
const originalFetch = globalThis.fetch;
beforeEach(() => {
  requests.length = 0;
  vi.stubEnv('VITE_ENABLE_RESEARCH_API', 'true');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      requests.push(input);
      return new Response(fixtureResponse(new URL(input, 'http://127.0.0.1')), {
        headers: { 'content-type': 'application/json' },
      });
    }),
  );
});
afterEach(() => {
  vi.stubGlobal('fetch', originalFetch);
  vi.unstubAllEnvs();
});
function setup(scenario: 'normal' | 'error' | 'loading' = 'normal') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = render(
    <QueryClientProvider client={client}>
      <DemoScenarioProvider initialScenario={scenario} adapter={createMockRepository(0)}>
        <TooltipProvider>
          <MemoryRouter initialEntries={['/research']}>
            <App />
          </MemoryRouter>
        </TooltipProvider>
      </DemoScenarioProvider>
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), client, ...ui };
}
async function enter(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'LOCAL API · READ ONLY' }));
}
describe('Research API view', () => {
  it('does not expose the switch or fetch without the opt-in flag', async () => {
    vi.stubEnv('VITE_ENABLE_RESEARCH_API', '');
    setup();
    await screen.findByRole(
      'heading',
      { name: '증거를 쌓고, 정책을 검증합니다.' },
      { timeout: 5000 },
    );
    expect(screen.queryByRole('group', { name: '연구 데이터 출처' })).not.toBeInTheDocument();
    expect(requests).toEqual([]);
  });
  it('defaults to DEMO without HTTP and gates production availability', async () => {
    setup();
    expect(
      await screen.findByRole(
        'heading',
        { name: '증거를 쌓고, 정책을 검증합니다.' },
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
    expect(requests).toEqual([]);
    expect(researchApiAvailable(false, 'true')).toBe(false);
    expect(researchApiAvailable(true, 'false')).toBe(false);
    expect(researchApiAvailable(true, 'true')).toBe(true);
  });
  it('renders all API sections without fictitious evidence and restores demo after navigation', async () => {
    const { user } = setup();
    await enter(user);
    expect(await screen.findByRole('table', { name: 'API 연구 후보 목록' })).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'API Snapshot 목록' })).toBeInTheDocument();
    expect(screen.getByText('baseline-a')).toBeInTheDocument();
    expect(screen.getByText('baseline-b')).toBeInTheDocument();
    expect(screen.getByText(/현재 API 미제공/)).toBeInTheDocument();
    expect(
      screen.queryByRole('region', { name: '현재 Runtime Ranking 정책' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('BASELINE', { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('데모 데이터 상태')).not.toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: '대시보드' }));
    expect(
      await screen.findByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' }),
    ).toBeInTheDocument();
    expect(screen.getByText('DEMO', { exact: true })).toBeInTheDocument();
  });
  it.each(['error', 'loading'] as const)('is independent of global Mock %s', async (scenario) => {
    const { user } = setup(scenario);
    await enter(user);
    expect(await screen.findByRole('table', { name: 'API 연구 후보 목록' })).toBeInTheDocument();
    expect(screen.queryByText('데모 데이터를 불러오지 못했습니다')).not.toBeInTheDocument();
  });
  it('uses server paging, search, sorting and snapshot page keys', async () => {
    const { user } = setup();
    await enter(user);
    await screen.findByRole('table', { name: 'API 연구 후보 목록' });
    await user.click(screen.getByRole('button', { name: '다음 후보 페이지' }));
    await screen.findByText('API Policy 20');
    expect(requests.some((url) => url.includes('candidates?page=1'))).toBe(true);
    await user.type(screen.getByLabelText('정책명 검색'), 'momentum &');
    await user.click(screen.getByRole('button', { name: '검색 적용' }));
    await waitFor(() => expect(requests.at(-1)).toContain('keyword=momentum'));
    expect(requests.at(-1)).toContain('page=0');
    await user.selectOptions(screen.getByLabelText('후보 정렬'), 'id,asc');
    await waitFor(() => expect(requests.at(-1)).toContain('sort=id%2Casc'));
    await user.selectOptions(screen.getByLabelText('후보 페이지 크기'), '10');
    await waitFor(() => expect(requests.at(-1)).toContain('size=10'));
    await user.click(screen.getByRole('button', { name: '다음 Snapshot 페이지' }));
    await waitFor(() => expect(requests.at(-1)).toContain('snapshots?page=1'));
  });
  it('loads exact detail ID, preserves weights, explains historical Gate and restores focus', async () => {
    const { user } = setup();
    await enter(user);
    const open = await screen.findByRole('button', { name: 'API Policy API 상세보기' });
    await user.click(open);
    const dialog = screen.getByRole('dialog', { name: '연구 후보 상세' });
    expect(await within(dialog).findByText('0.123456789123456789')).toBeInTheDocument();
    expect(within(dialog).getByText('저장된 당시 Gate Status')).toBeInTheDocument();
    expect(within(dialog).queryByRole('tab', { name: 'Forward' })).not.toBeInTheDocument();
    expect(requests).toContain('/api/v1/research/candidates/' + bigId);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(open).toHaveFocus();
  });
  it.each([401, 403, 500])(
    'shows HTTP %i safely without automatic retries or demo fallback',
    async (status) => {
      const fetcher = vi.fn(async () => new Response('hidden exception', { status }));
      vi.stubGlobal('fetch', fetcher);
      const { user } = setup();
      await enter(user);
      if (status === 401) {
        expect(await screen.findByRole('heading', { name: '관리자 로그인' })).toBeInTheDocument();
        expect(screen.queryByRole('table')).not.toBeInTheDocument();
        return;
      }
      await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(4));
      expect(fetcher).toHaveBeenCalledTimes(4);
      expect(screen.queryByText('hidden exception')).not.toBeInTheDocument();
      expect(screen.queryByText('BASELINE', { exact: true })).not.toBeInTheDocument();

      await user.click(screen.getAllByRole('button', { name: '다시 시도' })[0]);
      await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(5));
    },
  );
  it('represents empty lists and unobserved checkpoints without worker failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (input: string) =>
          new Response(
            input.includes('automation-status')
              ? emptyAutomationBody
              : input.includes('summary')
                ? fixtureResponse(new URL(input, 'http://127.0.0.1'))
                : pageBody([]),
            { headers: { 'content-type': 'application/json' } },
          ),
      ),
    );
    const { user } = setup();
    await enter(user);
    expect(await screen.findByText('조회된 후보가 없습니다')).toBeInTheDocument();
    expect(screen.getByText('미관찰 · 저장된 체크포인트가 없습니다')).toBeInTheDocument();
  });
  it('does not relabel stale success as current success after failed refresh', async () => {
    const { user } = setup();
    await enter(user);
    await screen.findByRole('table', { name: 'API 연구 후보 목록' });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('', { status: 500 })),
    );
    await user.click(screen.getByRole('button', { name: 'API 새로고침' }));
    await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(4));
    expect(screen.queryByRole('table', { name: 'API 연구 후보 목록' })).not.toBeInTheDocument();
  });
  it('cancels obsolete requests when leaving API mode', async () => {
    const signals: AbortSignal[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_input, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            const signal = init.signal!;
            signals.push(signal);
            signal.addEventListener('abort', () =>
              reject(new DOMException('aborted', 'AbortError')),
            );
          }),
      ),
    );
    const { user } = setup();
    await enter(user);
    await waitFor(() => expect(signals).toHaveLength(4));
    expect(screen.getAllByText('API 데이터 조회 중…')).toHaveLength(4);
    await user.click(screen.getByRole('button', { name: 'DEMO 화면' }));
    await waitFor(() => expect(signals.every((s) => s.aborted)).toBe(true));
  });
  it('does not let a late old filter response replace the new result', async () => {
    let releaseOld: ((response: Response) => void) | undefined;
    let oldSignal: AbortSignal | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string, init: RequestInit) => {
        const url = new URL(input, 'http://127.0.0.1');
        if (url.pathname.endsWith('/candidates') && !url.searchParams.has('keyword')) {
          oldSignal = init.signal ?? undefined;
          // Deliberately ignore abort in this fake transport and resolve late.
          return new Promise<Response>((resolve) => {
            releaseOld = resolve;
          });
        }
        return Promise.resolve(
          new Response(fixtureResponse(url), { headers: { 'content-type': 'application/json' } }),
        );
      }),
    );
    const { user } = setup();
    await enter(user);
    await waitFor(() => expect(releaseOld).toBeDefined());
    await user.type(screen.getByLabelText('정책명 검색'), 'new result');
    await user.click(screen.getByRole('button', { name: '검색 적용' }));
    await screen.findByRole('button', { name: 'API Policy API 상세보기' });
    expect(oldSignal?.aborted).toBe(true);
    await act(async () => {
      releaseOld!(
        new Response(pageBody([candidateBody(bigId, 'Obsolete Policy')]), {
          headers: { 'content-type': 'application/json' },
        }),
      );
    });
    expect(screen.queryByText('Obsolete Policy')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'API Policy API 상세보기' })).toBeInTheDocument();
  });
});
