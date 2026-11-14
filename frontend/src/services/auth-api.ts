export const SESSION_EXPIRED = 'ctb:session-expired';
export function reportUnauthorized(status: number, signal?: AbortSignal) {
  if (status === 401 && !signal?.aborted) window.dispatchEvent(new Event(SESSION_EXPIRED));
}
export function authAvailable() {
  return (
    import.meta.env.DEV &&
    [
      import.meta.env.VITE_ENABLE_RESEARCH_API,
      import.meta.env.VITE_ENABLE_OVERVIEW_API,
      import.meta.env.VITE_ENABLE_AI_ANALYSIS_API,
    ].some((flag) => flag === 'true')
  );
}
export class AuthError extends Error {
  constructor(public readonly status?: number) {
    super('Authentication request failed');
  }
}
async function request(path: 'me' | 'csrf' | 'login' | 'logout', init?: RequestInit) {
  if (!authAvailable()) throw new AuthError();
  const response = await fetch('/api/v1/auth/' + path, {
    ...init,
    credentials: 'same-origin',
    redirect: 'error',
    cache: 'no-store',
  });
  if (!response.ok) throw new AuthError(response.status);
  return response;
}
async function json(response: Response): Promise<Record<string, unknown>> {
  if (!response.headers.get('content-type')?.includes('application/json')) throw new AuthError();
  const value: unknown = await response.json();
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new AuthError();
  return value as Record<string, unknown>;
}
export async function currentAdmin(signal?: AbortSignal): Promise<string> {
  const value = await json(await request('me', { signal }));
  if (typeof value.username !== 'string' || !value.username.trim()) throw new AuthError();
  return value.username;
}
async function changeSession(path: 'login' | 'logout', body?: URLSearchParams) {
  // Never reuse a token across authentication/logout; Spring rotates it.
  const csrf = await json(await request('csrf'));
  if (csrf.headerName !== 'X-CSRF-TOKEN' || typeof csrf.token !== 'string' || !csrf.token)
    throw new AuthError();
  await request(path, { method: 'POST', headers: { 'X-CSRF-TOKEN': csrf.token }, body });
}
export async function loginAdmin(username: string, password: string) {
  await changeSession('login', new URLSearchParams({ username, password }));
  return currentAdmin();
}
export const logoutAdmin = () => changeSession('logout');
