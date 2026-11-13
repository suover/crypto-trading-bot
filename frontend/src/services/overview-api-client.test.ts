import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  overviewApi,
  OverviewApiError,
  performanceQuery,
  recommendationQuery,
} from './overview-api-client';
import {
  overviewSummary,
  overviewPositions,
  overviewFixtureResponse,
} from '@/test/overview-api-fixtures';
const response = (data: unknown) =>
  new Response(JSON.stringify(data), { headers: { 'content-type': 'application/json' } });
afterEach(() => vi.unstubAllGlobals());
describe('Overview GET client', () => {
  it('uses same-origin GET and exact string data without credentials in headers', async () => {
    const fetcher = vi.fn(async () => response(overviewSummary));
    vi.stubGlobal('fetch', fetcher);
    const signal = new AbortController().signal;
    await overviewApi.summary(signal);
    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/overview/summary',
      expect.objectContaining({
        method: 'GET',
        credentials: 'same-origin',
        redirect: 'error',
        cache: 'no-store',
        signal,
        headers: { Accept: 'application/json' },
      }),
    );
  });
  it.each([401, 403, 500])('does not automatically retry HTTP %i', async (status) => {
    const fetcher = vi.fn(async () => new Response('sensitive error', { status }));
    vi.stubGlobal('fetch', fetcher);
    await expect(overviewApi.positions(overviewSummary)).rejects.toMatchObject({
      kind: 'http',
      status,
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('retries a snapshot mismatch only once and then fails closed', async () => {
    const mismatch = {
      ...overviewPositions,
      portfolio: { ...overviewPositions.portfolio, snapshotId: '2' },
      items: [],
    };
    const fetcher = vi.fn(async () => response(mismatch));
    vi.stubGlobal('fetch', fetcher);
    await expect(overviewApi.positions(overviewSummary)).rejects.toMatchObject({
      kind: 'consistency',
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('accepts the second response if the snapshot now matches', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(response({ ...overviewPositions, portfolio: null, items: [] }))
      .mockResolvedValueOnce(response(overviewPositions));
    vi.stubGlobal('fetch', fetcher);
    expect((await overviewApi.positions(overviewSummary)).items).toHaveLength(3);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('rejects scope mismatch without retry', async () => {
    const fetcher = vi.fn(async () =>
      response({ ...overviewPositions, scope: { ...overviewPositions.scope, userId: '2' } }),
    );
    vi.stubGlobal('fetch', fetcher);
    await expect(overviewApi.positions(overviewSummary)).rejects.toMatchObject({
      kind: 'consistency',
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('rejects content type/schema and sanitizes network errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{}')),
    );
    await expect(overviewApi.summary()).rejects.toMatchObject({ kind: 'data' });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response({})),
    );
    await expect(overviewApi.summary()).rejects.toMatchObject({ kind: 'data' });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('private connection');
      }),
    );
    await expect(overviewApi.summary()).rejects.toEqual(new OverviewApiError('network'));
  });
  it('preserves cancellation and makes no request with an already aborted positions signal', async () => {
    const controller = new AbortController();
    controller.abort();
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    await expect(overviewApi.positions(overviewSummary, controller.signal)).rejects.toMatchObject({
      name: 'AbortError',
    });
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('validates page/date/action and encodes conditions', () => {
    expect(() => performanceQuery({ from: 'invalid', to: '', page: 0, size: 90 })).toThrow();
    expect(() =>
      performanceQuery({
        from: '2026-02-01T00:00:00Z',
        to: '2026-01-01T00:00:00Z',
        page: 0,
        size: 90,
      }),
    ).toThrow();
    expect(() =>
      recommendationQuery({ action: '', page: Number.MAX_SAFE_INTEGER, size: 50 }),
    ).toThrow();
    expect(() => recommendationQuery({ action: '', page: 0, size: 51 })).toThrow();
    expect(recommendationQuery({ action: 'HOLD', page: 0, size: 10 })).toBe(
      'page=0&size=10&action=HOLD',
    );
  });
  it('rejects a valid response from the wrong requested page or action', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(overviewFixtureResponse(new URL('http://local/recommendations')), {
            headers: { 'content-type': 'application/json' },
          }),
      ),
    );
    await expect(
      overviewApi.recommendations({ page: 1, size: 10, action: '' }),
    ).rejects.toMatchObject({ kind: 'data' });
    await expect(
      overviewApi.recommendations({ page: 0, size: 10, action: 'BUY' }),
    ).rejects.toMatchObject({ kind: 'data' });
  });
});
