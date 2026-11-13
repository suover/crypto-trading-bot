import { afterEach, describe, it, expect, vi } from 'vitest';
import { analysisApi, analysisSearchQuery, instantOrder } from './analysis-api-client';
import { parseAnalysisDetail } from '@/lib/analysis-api-parser';
import {
  analysisDetail,
  analysisPage,
  analysisRecommendation,
  analysisCandidate,
  analysisUniverse,
  analysisBigId,
} from '@/test/analysis-api-fixtures';
const filter = { from: '', to: '', market: '', action: '' as const, page: 0, size: 20 };
const response = (data: unknown) =>
  new Response(JSON.stringify(data), { headers: { 'content-type': 'application/json' } });
afterEach(() => vi.unstubAllGlobals());
describe('AI Analysis GET boundary', () => {
  it('uses only same-origin GET, abort signal and no redirects/cache', async () => {
    const fetcher = vi.fn().mockResolvedValue(response(analysisPage([analysisRecommendation])));
    vi.stubGlobal('fetch', fetcher);
    const controller = new AbortController();
    await analysisApi.recommendations(filter, controller.signal);
    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/ai-trade-analysis/recommendations?page=0&size=20',
      expect.objectContaining({
        method: 'GET',
        credentials: 'same-origin',
        redirect: 'error',
        cache: 'no-store',
        signal: controller.signal,
      }),
    );
  });
  it.each([401, 403, 404, 500])('keeps HTTP %s explicit and sanitizes body', async (status) => {
    const fetcher = vi.fn().mockResolvedValue(new Response('private exception', { status }));
    vi.stubGlobal('fetch', fetcher);
    await expect(analysisApi.detail({ id: analysisBigId })).rejects.toMatchObject({
      status,
      kind: 'http',
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it.each([
    { page: -1 },
    { size: 101 },
    { market: 'BTC' },
    { from: 'invalid' },
    { from: '2026-02-01T00:00:00Z', to: '2026-01-01T00:00:00Z' },
  ])('rejects invalid inputs %# without fetch', (patch) => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    expect(() => analysisApi.recommendations({ ...filter, ...patch })).toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('validates request ID, returned ID, scope and exact universe across requests', async () => {
    const fetcher = vi.fn().mockImplementation(async () => response(analysisDetail));
    vi.stubGlobal('fetch', fetcher);
    expect(() => analysisApi.detail({ id: '../approve' })).toThrow();
    await expect(analysisApi.detail({ id: '2' })).rejects.toMatchObject({ kind: 'consistency' });
    await expect(
      analysisApi.detail({ id: analysisBigId, scope: { ...analysisDetail.scope, userId: '2' } }),
    ).rejects.toMatchObject({ kind: 'consistency' });
    const detail = parseAnalysisDetail(JSON.stringify(analysisDetail));
    for (const patch of [
      { scope: { ...analysisDetail.scope, userId: '2' } },
      { universe: { ...analysisUniverse, selectedCandidateId: '8002' } },
      {
        universe: {
          ...analysisUniverse,
          status: 'NO_REFERENCE',
          analysisRunId: null,
          selectedCandidateId: null,
        },
        items: [],
        totalElements: 0,
        totalPages: 0,
      },
    ]) {
      fetcher.mockImplementation(async () =>
        response({ ...analysisPage([analysisCandidate]), universe: analysisUniverse, ...patch }),
      );
      await expect(analysisApi.candidates(detail, 0, 20)).rejects.toMatchObject({
        kind: 'consistency',
      });
    }
  });
  it('checks page, action and market echo', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async () => response(analysisPage([analysisRecommendation]))),
    );
    await expect(analysisApi.recommendations({ ...filter, page: 1 })).rejects.toMatchObject({
      kind: 'data',
    });
    await expect(analysisApi.recommendations({ ...filter, action: 'SELL' })).rejects.toMatchObject({
      kind: 'data',
    });
    await expect(
      analysisApi.recommendations({ ...filter, market: 'KRW-ETH' }),
    ).rejects.toMatchObject({ kind: 'data' });
  });
  it('does not lose nanosecond boundaries or treat fractional and whole seconds incorrectly', () => {
    expect(instantOrder('2026-01-01T00:00:00Z', '2026-01-01T00:00:00.000Z')).toBe(0);
    expect(instantOrder('2026-01-01T00:00:00.000000001Z', '2026-01-01T00:00:00Z')).toBe(1);
    expect(
      analysisSearchQuery({
        ...filter,
        from: '2026-01-01T00:00:00Z',
        to: '2026-01-01T00:00:00.001Z',
      }),
    ).toContain('from=');
  });
  it('rejects non-JSON, network and bad schema without raw error disclosure', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('<html>private</html>'));
    vi.stubGlobal('fetch', fetcher);
    await expect(analysisApi.detail({ id: '1' })).rejects.toMatchObject({ kind: 'data' });
    fetcher.mockRejectedValue(new Error('private url'));
    await expect(analysisApi.detail({ id: '1' })).rejects.toMatchObject({ kind: 'network' });
    fetcher.mockResolvedValue(response({}));
    await expect(analysisApi.detail({ id: '1' })).rejects.toMatchObject({ kind: 'data' });
  });
});
