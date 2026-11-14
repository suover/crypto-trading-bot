import { afterEach, expect, it, vi } from 'vitest';
import {
  currentAdmin,
  loginAdmin,
  logoutAdmin,
  reportUnauthorized,
  SESSION_EXPIRED,
} from './auth-api';
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
const json = (value: unknown) =>
  new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } });
it('obtains fresh CSRF per mutation and never persists a token', async () => {
  vi.stubEnv('VITE_ENABLE_RESEARCH_API', 'true');
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push(url);
      expect(init.redirect).toBe('error');
      if (url.endsWith('/csrf'))
        return json({ headerName: 'X-CSRF-TOKEN', token: 'masked-' + calls.length });
      if (url.endsWith('/me')) return json({ username: 'fixture-admin' });
      expect(init.method).toBe('POST');
      expect(init.headers).toEqual({
        'X-CSRF-TOKEN': url.endsWith('/login') ? 'masked-1' : 'masked-4',
      });
      return new Response(null, { status: 204 });
    }),
  );
  expect(await loginAdmin('fixture-admin', 'fixture-only')).toBe('fixture-admin');
  await logoutAdmin();
  expect(calls).toEqual(
    ['csrf', 'login', 'me', 'csrf', 'logout'].map((path) => '/api/v1/auth/' + path),
  );
});
it.each([
  {},
  null,
  { headerName: 'Authorization', token: 'bad' },
  { headerName: 'X-CSRF-TOKEN', token: '' },
])('fails closed on malformed CSRF %j', async (value) => {
  vi.stubEnv('VITE_ENABLE_RESEARCH_API', 'true');
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => json(value)),
  );
  await expect(loginAdmin('a', 'b')).rejects.toThrow();
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('does not publish 401 from an aborted old request', () => {
  const listener = vi.fn();
  window.addEventListener(SESSION_EXPIRED, listener);
  const controller = new AbortController();
  controller.abort();
  reportUnauthorized(401, controller.signal);
  reportUnauthorized(403);
  expect(listener).not.toHaveBeenCalled();
  reportUnauthorized(401);
  expect(listener).toHaveBeenCalledTimes(1);
  window.removeEventListener(SESSION_EXPIRED, listener);
});
it('never sends auth requests in production mode', async () => {
  vi.stubEnv('DEV', false);
  vi.stubEnv('VITE_ENABLE_RESEARCH_API', 'true');
  vi.stubGlobal('fetch', vi.fn());
  await expect(currentAdmin()).rejects.toThrow();
  expect(fetch).not.toHaveBeenCalled();
});
