import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { App } from '@/app/App';
import { DemoScenarioProvider } from '@/hooks/DemoScenarioProvider';
import { createMockRepository } from '@/services/mock-dashboard-repository';
import { TooltipProvider } from '@/components/ui/tooltip';
import { overviewFixtureResponse } from '@/test/overview-api-fixtures';
import { SESSION_EXPIRED } from '@/services/auth-api';
import { analysisFixtureResponse, analysisBigId } from '@/test/analysis-api-fixtures';
import { fixtureResponse } from '@/test/research-api-fixtures';
import { StrictMode } from 'react';

let authenticated = false;
let loginStatus = 204;
let logoutStatus = 204;
let businessReads: string[];
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
beforeEach(() => {
  authenticated = false;
  loginStatus = 204;
  logoutStatus = 204;
  businessReads = [];
  vi.stubEnv('VITE_ENABLE_OVERVIEW_API', 'true');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init?: RequestInit) => {
      expect(init?.credentials).toBe('same-origin');
      expect(init?.redirect).toBe('error');
      expect(init?.cache).toBe('no-store');
      if (path.endsWith('/auth/me'))
        return authenticated ? json({ username: 'fixture-admin' }) : json({}, 401);
      if (path.endsWith('/auth/csrf'))
        return json({ headerName: 'X-CSRF-TOKEN', token: 'masked-fixture-token' });
      if (path.endsWith('/auth/login')) {
        expect(init?.method).toBe('POST');
        expect(init?.headers).toEqual({ 'X-CSRF-TOKEN': 'masked-fixture-token' });
        expect(init?.body).toBeInstanceOf(URLSearchParams);
        authenticated = loginStatus === 204;
        return new Response(null, { status: loginStatus });
      }
      if (path.endsWith('/auth/logout')) {
        expect(init?.method).toBe('POST');
        expect(init?.headers).toEqual({ 'X-CSRF-TOKEN': 'masked-fixture-token' });
        if (logoutStatus === 204) authenticated = false;
        return new Response(null, { status: logoutStatus });
      }
      if (!authenticated) throw new Error('Business request before authentication');
      businessReads.push(path);
      const url = new URL(path, 'http://local');
      const body = path.includes('/research/')
        ? fixtureResponse(url)
        : path.includes('/ai-trade-analysis/')
          ? analysisFixtureResponse(url)
          : overviewFixtureResponse(url);
      return new Response(body, {
        headers: { 'content-type': 'application/json' },
      });
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
function setup(path = '/login', state?: unknown) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DemoScenarioProvider adapter={createMockRepository(0)}>
        <TooltipProvider>
          <MemoryRouter
            initialEntries={[
              {
                pathname: path.split('?')[0],
                search: path.includes('?') ? '?' + path.split('?')[1] : '',
                state,
              },
            ]}
          >
            <StrictMode>
              <App />
            </StrictMode>
          </MemoryRouter>
        </TooltipProvider>
      </DemoScenarioProvider>
    </QueryClientProvider>,
  );
  return { client, user: userEvent.setup() };
}
async function enterCredentials(
  user: ReturnType<typeof userEvent.setup>,
  username = 'fixture-admin',
) {
  await user.type(await screen.findByLabelText('관리자 아이디'), username);
  await user.type(screen.getByLabelText('비밀번호'), 'fixture-password');
  await user.click(screen.getByRole('button', { name: '로그인' }));
}
it('shows guest demo, automatically selects user data on login, and clears it on logout', async () => {
  const { user, client } = setup('/overview');
  await screen.findByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' });
  await user.click(screen.getByRole('link', { name: '관리자 로그인' }));
  await screen.findByLabelText('관리자 아이디');
  expect(businessReads).toEqual([]);
  await enterCredentials(user);
  await screen.findByRole('button', { name: '로그아웃' });
  await waitFor(() => expect(businessReads.length).toBeGreaterThan(0));
  client.setQueryData(['sensitive-fixture'], 'private-value');
  await user.click(screen.getByRole('button', { name: '로그아웃' }));
  await screen.findByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' });
  expect(client.getQueryData(['sensitive-fixture'])).toBeUndefined();
  expect(screen.queryByRole('table', { name: 'API 보유 자산' })).not.toBeInTheDocument();
  expect(authenticated).toBe(false);
});
it.each([401, 403, 429, 500])(
  'shows sanitized login failure %s and clears password',
  async (status) => {
    loginStatus = status;
    const { user } = setup();
    await enterCredentials(user);
    await screen.findByRole('alert');
    expect(screen.getByLabelText('비밀번호')).toHaveValue('');
    expect(businessReads).toEqual([]);
    expect(authenticated).toBe(false);
  },
);
it('restores an existing server session and rejects external return paths', async () => {
  authenticated = true;
  setup('/login', { returnTo: '//evil.example' });
  await screen.findByRole('table', { name: 'API 보유 자산' }, { timeout: 5000 });
  expect(screen.queryByLabelText('비밀번호')).not.toBeInTheDocument();
  expect(businessReads.length).toBeGreaterThan(0);
});
it('session expiration clears cached data and returns to demo', async () => {
  authenticated = true;
  const { client } = setup('/overview');
  await screen.findByRole('button', { name: '로그아웃' });
  client.setQueryData(['sensitive-fixture'], 'private');
  act(() => {
    authenticated = false;
    window.dispatchEvent(new Event(SESSION_EXPIRED));
  });
  await screen.findByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' });
  expect(client.getQueryData(['sensitive-fixture'])).toBeUndefined();
  expect(screen.queryByRole('table', { name: 'API 보유 자산' })).not.toBeInTheDocument();
});
it('failed logout hides data without claiming that the server session ended', async () => {
  authenticated = true;
  logoutStatus = 500;
  const { user, client } = setup('/overview');
  await screen.findByRole('button', { name: '로그아웃' });
  client.setQueryData(['sensitive-fixture'], 'private');
  await user.click(screen.getByRole('button', { name: '로그아웃' }));
  await screen.findByText(/로그아웃 결과를 확인/);
  expect(client.getQueryData(['sensitive-fixture'])).toBeUndefined();
  expect(screen.queryByRole('table', { name: 'API 보유 자산' })).not.toBeInTheDocument();
  expect(authenticated).toBe(true);
});
it('production/disabled API remains DEMO-only with no auth calls', async () => {
  vi.stubEnv('DEV', false);
  setup();
  await screen.findByText(/이 환경은 DEMO 전용/);
  expect(fetch).not.toHaveBeenCalled();
});

it('logout aborts in-flight business requests before clearing their cache', async () => {
  authenticated = true;
  const normalFetch = globalThis.fetch;
  const signals: AbortSignal[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init: RequestInit) => {
      if (path.includes('/auth/')) return normalFetch(path, init);
      const signal = init.signal!;
      signals.push(signal);
      return new Promise<Response>((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
      });
    }),
  );
  const { user } = setup('/overview');
  await waitFor(() => expect(signals.length).toBeGreaterThan(0));
  await user.click(screen.getByRole('button', { name: '로그아웃' }));
  await screen.findByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' });
  expect(signals.every((signal) => signal.aborted)).toBe(true);
});

it.each([
  ['/research', '증거를 쌓고, 정책을 검증합니다.', 'API 연구 후보 목록'],
  ['/ai-analysis', '판단의 근거를, 한눈에', 'API AI 추천'],
])(
  'returns to %s after login and shows its API without a source switch',
  async (path, heading, table) => {
    vi.stubEnv('VITE_ENABLE_RESEARCH_API', 'true');
    vi.stubEnv('VITE_ENABLE_AI_ANALYSIS_API', 'true');
    const { user } = setup(path);
    await screen.findByRole('heading', { name: heading });
    expect(businessReads).toEqual([]);
    await user.click(screen.getByRole('link', { name: '관리자 로그인' }));
    await enterCredentials(user);
    await screen.findByRole('table', { name: table });
    expect(screen.queryByRole('button', { name: 'LOCAL API · READ ONLY' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '로그아웃' }));
    await screen.findByRole('heading', { name: heading });
    expect(screen.queryByRole('table', { name: table })).not.toBeInTheDocument();
  },
);

it('preserves the real recommendation deep link across login without opening a demo detail', async () => {
  vi.stubEnv('VITE_ENABLE_AI_ANALYSIS_API', 'true');
  const { user } = setup(`/ai-analysis?recommendation=${analysisBigId}`);
  await screen.findByText(/실제 추천 상세 링크입니다/);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(businessReads).toEqual([]);
  await user.click(screen.getByRole('link', { name: '관리자 로그인' }));
  await enterCredentials(user);
  await screen.findByText('Application safety checked reason');
  expect(screen.queryByText('DETAIL VIEW · DEMO')).not.toBeInTheDocument();
});

it.each([
  '/portfolio',
  '/operations',
  '/research',
  '/ai-analysis',
  '/portfolio/',
  '/operations/',
  '/Portfolio',
  '/%70ortfolio',
])('does not present mock figures as user data on unconnected %s', async (path) => {
  authenticated = true;
  setup(path);
  await screen.findByRole('heading', { name: '실제 데이터 미연동' });
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('데모 데이터 상태')).not.toBeInTheDocument();
  expect(businessReads).toEqual([]);
});

it('opens the root route as the authenticated overview instead of an unconnected page', async () => {
  authenticated = true;
  setup('/');
  await screen.findByRole('table', { name: 'API 보유 자산' });
  expect(screen.queryByRole('heading', { name: '실제 데이터 미연동' })).not.toBeInTheDocument();
});

it('does not flash demo or fetch protected data while the session is unresolved', async () => {
  let resolveSession!: (response: Response) => void;
  const normalFetch = globalThis.fetch;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init: RequestInit) => {
      if (path.endsWith('/auth/me'))
        return new Promise<Response>((resolve) => {
          resolveSession = resolve;
        });
      return normalFetch(path, init);
    }),
  );
  setup('/overview');
  await screen.findByText('로그인 상태 확인 중…');
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  expect(screen.queryByText('DEMO', { exact: true })).not.toBeInTheDocument();
  expect(businessReads).toEqual([]);
  await act(async () => {
    resolveSession(json({}, 401));
  });
  await screen.findByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' });
});

it.each([403, 500, 'network'] as const)(
  'keeps session failure %s distinct from guest demo and allows recovery',
  async (failure) => {
    const normalFetch = globalThis.fetch;
    let failed = true;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, init: RequestInit) => {
        if (path.endsWith('/auth/me') && failed) {
          return failure === 'network'
            ? Promise.reject(new TypeError('Failed to fetch'))
            : Promise.resolve(json({}, failure));
        }
        return normalFetch(path, init);
      }),
    );
    const { user } = setup('/overview');
    await screen.findByRole('alert');
    expect(screen.queryByText('DEMO', { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(businessReads).toEqual([]);
    failed = false;
    await user.click(screen.getByRole('button', { name: '인증 상태 다시 확인' }));
    await screen.findByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' });
  },
);

it('keeps standalone demo usable without a backend or any authentication requests', async () => {
  vi.stubEnv('VITE_ENABLE_OVERVIEW_API', '');
  setup('/overview');
  await screen.findByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' });
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.queryByRole('link', { name: '관리자 로그인' })).not.toBeInTheDocument();
});

it('hides private data immediately but waits for server logout before showing demo', async () => {
  authenticated = true;
  let finishLogout!: (response: Response) => void;
  const normalFetch = globalThis.fetch;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init: RequestInit) => {
      if (path.endsWith('/auth/logout')) {
        return new Promise<Response>((resolve) => {
          finishLogout = resolve;
        });
      }
      return normalFetch(path, init);
    }),
  );
  const { user, client } = setup('/overview');
  await screen.findByRole('table', { name: 'API 보유 자산' });
  await user.click(screen.getByRole('button', { name: '로그아웃' }));
  await waitFor(() => expect(finishLogout).toBeDefined());
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  expect(screen.queryByText('DEMO', { exact: true })).not.toBeInTheDocument();
  expect(client.getQueryCache().findAll({ queryKey: ['overview-local-api'] })).toHaveLength(0);
  await act(async () => {
    authenticated = false;
    finishLogout(new Response(null, { status: 204 }));
  });
  await screen.findByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' });
});

it.each([200, 401])(
  'ignores late account-A HTTP %s responses after account-B login',
  async (status) => {
    authenticated = true;
    let secondAccount = false;
    const pending: { finish: (response: Response) => void; signal: AbortSignal; path: string }[] =
      [];
    const normalFetch = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, init: RequestInit) => {
        if (path.endsWith('/auth/me') && authenticated) {
          return Promise.resolve(
            json({ username: secondAccount ? 'fixture-second' : 'fixture-admin' }),
          );
        }
        if (path.includes('/auth/')) return normalFetch(path, init);
        expect(authenticated).toBe(true);
        if (!secondAccount) {
          // Deliberately let the old transport complete even after cancellation.
          return new Promise<Response>((finish) => {
            pending.push({ finish, signal: init.signal!, path });
          });
        }
        const body = overviewFixtureResponse(new URL(path, 'http://local')).replaceAll(
          '"userId":"1"',
          '"userId":"20"',
        );
        return Promise.resolve(
          new Response(body, { headers: { 'content-type': 'application/json' } }),
        );
      }),
    );
    const { user, client } = setup('/overview');
    await waitFor(() => expect(pending.length).toBeGreaterThan(0));
    await user.click(screen.getByRole('button', { name: '로그아웃' }));
    await screen.findByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' });
    expect(pending.every(({ signal }) => signal.aborted)).toBe(true);
    secondAccount = true;
    await user.click(screen.getByRole('link', { name: '관리자 로그인' }));
    await enterCredentials(user, 'fixture-second');
    await screen.findByRole('table', { name: 'API 보유 자산' });
    await act(async () => {
      for (const { finish, path } of pending) {
        finish(
          new Response(overviewFixtureResponse(new URL(path, 'http://local')), {
            status,
            headers: { 'content-type': 'application/json' },
          }),
        );
      }
    });
    expect(screen.getByText('fixture-second')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '로그아웃' })).toBeInTheDocument();
    expect(client.getQueryData(['overview-local-api', 'summary'])).toMatchObject({
      scope: { userId: '20' },
    });
    expect(screen.queryByText('DEMO', { exact: true })).not.toBeInTheDocument();
  },
);
