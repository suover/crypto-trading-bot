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
      return new Response(overviewFixtureResponse(new URL(path, 'http://local')), {
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
          <MemoryRouter initialEntries={[{ pathname: path, state }]}>
            <App />
          </MemoryRouter>
        </TooltipProvider>
      </DemoScenarioProvider>
    </QueryClientProvider>,
  );
  return { client, user: userEvent.setup() };
}
async function enterCredentials(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText('관리자 아이디'), 'fixture-admin');
  await user.type(screen.getByLabelText('비밀번호'), 'fixture-password');
  await user.click(screen.getByRole('button', { name: '로그인' }));
}
it('gates API before fetching, logs in, returns to selected API, and clears cache on logout', async () => {
  const { user, client } = setup('/overview');
  expect(fetch).not.toHaveBeenCalled();
  await user.click(
    await screen.findByRole('button', { name: 'LOCAL API · READ ONLY' }, { timeout: 5000 }),
  );
  await screen.findByLabelText('관리자 아이디');
  expect(businessReads).toEqual([]);
  await enterCredentials(user);
  await screen.findByRole('button', { name: '로그아웃' });
  await waitFor(() => expect(businessReads.length).toBeGreaterThan(0));
  client.setQueryData(['sensitive-fixture'], 'private-value');
  await user.click(screen.getByRole('button', { name: '로그아웃' }));
  await screen.findByLabelText('관리자 아이디');
  expect(client.getQueryData(['sensitive-fixture'])).toBeUndefined();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
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
  await screen.findByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' }, { timeout: 5000 });
  expect(screen.queryByLabelText('비밀번호')).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('session expiration clears cached data and requires login again', async () => {
  authenticated = true;
  const { user, client } = setup('/overview');
  await user.click(
    await screen.findByRole('button', { name: 'LOCAL API · READ ONLY' }, { timeout: 5000 }),
  );
  await screen.findByRole('button', { name: '로그아웃' });
  client.setQueryData(['sensitive-fixture'], 'private');
  act(() => {
    authenticated = false;
    window.dispatchEvent(new Event(SESSION_EXPIRED));
  });
  await screen.findByLabelText('관리자 아이디');
  expect(client.getQueryData(['sensitive-fixture'])).toBeUndefined();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
});
it('failed logout hides data without claiming that the server session ended', async () => {
  authenticated = true;
  logoutStatus = 500;
  const { user, client } = setup('/overview');
  await user.click(
    await screen.findByRole('button', { name: 'LOCAL API · READ ONLY' }, { timeout: 5000 }),
  );
  await screen.findByRole('button', { name: '로그아웃' });
  client.setQueryData(['sensitive-fixture'], 'private');
  await user.click(screen.getByRole('button', { name: '로그아웃' }));
  await screen.findByText(/로그아웃 결과를 확인/);
  expect(client.getQueryData(['sensitive-fixture'])).toBeUndefined();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
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
  await user.click(
    await screen.findByRole('button', { name: 'LOCAL API · READ ONLY' }, { timeout: 5000 }),
  );
  await waitFor(() => expect(signals.length).toBeGreaterThan(0));
  await user.click(screen.getByRole('button', { name: '로그아웃' }));
  await screen.findByLabelText('관리자 아이디');
  expect(signals.every((signal) => signal.aborted)).toBe(true);
});
