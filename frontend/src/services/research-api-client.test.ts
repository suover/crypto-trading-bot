import { afterEach, describe, expect, it, vi } from 'vitest';
import { researchApi, candidateQuery } from './research-api-client';
import {
  parseDetail,
  parseSummary,
  parseCandidates,
  parseAutomation,
  timestamp,
  validId,
} from '@/lib/research-api-parser';
import { defaultFilters } from '@/types/research-api';
import {
  at,
  automationBody,
  bigId,
  candidateBody,
  detailBody,
  emptyAutomationBody,
  fixtureResponse,
  pageBody,
  referenceId,
  summaryBody,
} from '@/test/research-api-fixtures';
import { dateTime } from '@/lib/format';

const originalFetch = globalThis.fetch;
afterEach(() => vi.stubGlobal('fetch', originalFetch));
const ok = (body: string) =>
  new Response(body, { headers: { 'content-type': 'application/json' } });
describe('Research HTTP client and exact numeric parsing', () => {
  it('reads all five DTO contracts through GET relative paths', async () => {
    const fetcher = vi.fn(async (input: string) =>
      ok(fixtureResponse(new URL(input, 'http://127.0.0.1'))),
    );
    vi.stubGlobal('fetch', fetcher);
    expect((await researchApi.summary()).candidateCount).toBe('21');
    expect((await researchApi.candidates(defaultFilters)).items[0].id).toBe(bigId);
    expect((await researchApi.detail(bigId)).referenceSnapshotId).toBe(referenceId);
    expect((await researchApi.snapshots(0, 20)).items[0].id).toBe(referenceId);
    expect((await researchApi.automation()).states).toHaveLength(2);
    for (const [path, config] of fetcher.mock.calls as unknown as [string, RequestInit][]) {
      expect(path).toMatch(/^\/api\/v1\/research\//);
      expect(config).toMatchObject({
        method: 'GET',
        credentials: 'same-origin',
        redirect: 'error',
        cache: 'no-store',
      });
      expect(config.headers).not.toHaveProperty('Authorization');
    }
  });
  it('keeps Long and decimal numeric source text without a Number round-trip', () => {
    const d = parseDetail(detailBody);
    expect(d.id).toBe('9007199254740993');
    expect(d.referenceSnapshotId).toBe('9007199254740995');
    expect(d.id === d.referenceSnapshotId).toBe(false);
    expect(d.componentWeights).toEqual({
      liquidity: '0.123456789123456789',
      trend_alignment: '0.876543210987654321',
      volume_confirmation: '0',
    });
    expect(d.registeredAt).toBe(at);
    expect(dateTime(d.registeredAt)).toBe('2026.01.01 09:00');
  });
  it('serializes filters with proper URL encoding and exact detail ID', async () => {
    const f = {
      ...defaultFilters,
      keyword: 'a & 한글 +',
      registeredFrom: '2026-01-01T00:00:00Z',
      registeredTo: '2026-01-02T00:00:00Z',
      shadowEnrolled: 'false' as const,
      sort: 'policyName,asc' as const,
      page: 2,
      size: 10,
    };
    const params = new URLSearchParams(candidateQuery(f));
    for (const [k, v] of Object.entries(f)) expect(params.get(k)).toBe(String(v));
    const fetcher = vi.fn(async (input: string) =>
      ok(fixtureResponse(new URL(input, 'http://127.0.0.1'))),
    );
    vi.stubGlobal('fetch', fetcher);
    await researchApi.detail(bigId);
    expect(fetcher.mock.calls[0]?.[0]).toBe('/api/v1/research/candidates/9007199254740993');
  });
  it.each([400, 401, 403, 404, 500])('does not expose HTTP %i error bodies', async (status) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('sensitive server error', { status })),
    );
    await expect(researchApi.summary()).rejects.toMatchObject({ kind: 'http', status });
    await expect(researchApi.summary()).rejects.not.toThrow('sensitive');
  });
  it('classifies network and redirects as failures', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('failed fetch');
      }),
    );
    await expect(researchApi.summary()).rejects.toMatchObject({ kind: 'network' });
  });
  it.each([
    'not json',
    '{}',
    'null',
    summaryBody.replace('"candidateCount":21', '"candidateCount":"21"'),
  ])('fails closed for invalid body %s', async (body) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ok(body)),
    );
    await expect(researchApi.summary()).rejects.toMatchObject({ kind: 'data' });
  });
  it('does not accept HTML as an API response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<html/>')),
    );
    await expect(researchApi.summary()).rejects.toMatchObject({ kind: 'data' });
  });
  it('passes AbortSignal through and preserves cancellation', async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url, options: RequestInit) =>
          new Promise((_resolve, reject) => {
            expect(options.signal).toBe(controller.signal);
            options.signal?.addEventListener('abort', () =>
              reject(new DOMException('Aborted', 'AbortError')),
            );
          }),
      ),
    );
    const pending = researchApi.summary(controller.signal);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });
  it.each(['-1', '0', '1.1', '9223372036854775808', '1e3', '../summary', 'Infinity'])(
    'rejects invalid ID %s before any request',
    async (id) => {
      const fetcher = vi.fn();
      vi.stubGlobal('fetch', fetcher);
      expect(validId(id)).toBe(false);
      await expect(researchApi.detail(id)).rejects.toMatchObject({ kind: 'input' });
      expect(fetcher).not.toHaveBeenCalled();
    },
  );
  it.each(['-1', '1.5', '9223372036854775808', '1e9999'])(
    'rejects out-of-range response IDs %s',
    (value) => {
      expect(() => parseDetail(detailBody.replace('"id":' + bigId, '"id":' + value))).toThrow();
    },
  );
  it.each(['NaN', '1e9999', '-0.1', '2', '"0.1"'])('rejects invalid weights %s', (value) => {
    expect(() => parseDetail(detailBody.replace('0.123456789123456789', value))).toThrow();
  });
  it('distinguishes null and zero, all automation contexts, and absent observations', () => {
    expect(parseAutomation(automationBody).states[0].lastAttemptAt).toBeNull();
    expect(parseAutomation(emptyAutomationBody)).toMatchObject({ observed: false, states: [] });
    expect(parseCandidates(pageBody([])).totalElements).toBe('0');
    const empty = summaryBody
      .replace('"latestSnapshotId":' + referenceId, '"latestSnapshotId":null')
      .replace('"latestSnapshotCapturedAt":"' + at + '"', '"latestSnapshotCapturedAt":null');
    expect(parseSummary(empty).latestSnapshotId).toBeNull();
  });
  it('rejects invalid dates, impossible pagination, mismatched enrollment and returned identity', async () => {
    expect(() => timestamp('2026-02-30T00:00:00Z')).toThrow();
    expect(() => parseCandidates(pageBody([candidateBody()], 0, 20, 0))).toThrow();
    expect(() =>
      parseDetail(detailBody.replace('"shadowEnrolled":true', '"shadowEnrolled":false')),
    ).toThrow();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ok(detailBody)),
    );
    await expect(researchApi.detail(referenceId)).rejects.toMatchObject({ kind: 'data' });
  });
});
