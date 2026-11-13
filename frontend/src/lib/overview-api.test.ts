import { describe, expect, it } from 'vitest';
import {
  parseSummary,
  parsePositions,
  parsePerformance,
  parseRecommendations,
} from './overview-api-parser';
import {
  decimalText,
  moneyText,
  percentText,
  ratioText,
  valueTone,
  kstInstant,
} from './overview-format';
import {
  overviewSummary,
  overviewPositions,
  overviewFixtureResponse,
  performanceRow,
  overviewBigId,
} from '@/test/overview-api-fixtures';
import { performanceSeries } from '@/pages/overview/performance-series';

describe('Overview response contract', () => {
  it('preserves strings, null, separate timestamps and partial statuses', () => {
    const data = parseSummary(JSON.stringify(overviewSummary));
    expect(data.portfolio?.snapshotId).toBe(overviewBigId);
    expect(data.portfolio?.knownTotalValueKrw).toBe('12345678901234567890.1234567890');
    expect(data.portfolio?.totalValueKrw).toBeNull();
    expect(data.performance?.performanceStatus).toBe('PARTIAL');
    expect(data.botPnl?.calculatedAt).not.toBe(data.portfolio?.capturedAt);
    expect(data.generatedAt).not.toBe(data.portfolio?.capturedAt);
  });
  it.each(['COMPLETE', 'BASELINE'] as const)(
    'preserves %s and zero without reinterpreting performance',
    (status) => {
      const input = structuredClone(overviewSummary);
      input.performance = { ...performanceRow(1, status), portfolioSnapshotId: overviewBigId };
      input.portfolio!.totalValueKrw = '0.0000000000';
      const result = parseSummary(JSON.stringify(input));
      expect(result.portfolio?.totalValueKrw).toBe('0.0000000000');
      expect(result.performance?.performanceStatus).toBe(status);
    },
  );
  it('keeps unpriced, excluded, tiny quantities, and missing basis', () => {
    const result = parsePositions(JSON.stringify(overviewPositions));
    expect(result.items.map((p) => p.valuationStatus)).toEqual(['PRICED', 'UNPRICED', 'EXCLUDED']);
    expect(result.items[0].totalQuantity).toBe('0.0000000001');
    expect(result.items[1].estimatedCostBasisKrw).toBeNull();
  });
  it('separates absent snapshots from empty holdings', () => {
    const empty = { ...overviewPositions, portfolio: null, items: [] };
    expect(parsePositions(JSON.stringify(empty)).portfolio).toBeNull();
    expect(
      parsePositions(JSON.stringify({ ...empty, portfolio: overviewPositions.portfolio }))
        .portfolio,
    ).not.toBeNull();
    expect(
      parseSummary(
        JSON.stringify({ ...overviewSummary, portfolio: null, performance: null, botPnl: null }),
      ).portfolio,
    ).toBeNull();
  });
  it.each([1, 'NaN', 'Infinity', '-Infinity', '1e3', '', '0.00000000001', '1'.repeat(31)])(
    'rejects invalid decimal %s',
    (value) => {
      const input = structuredClone(overviewSummary) as unknown as {
        portfolio: { totalValueKrw: unknown };
      };
      input.portfolio.totalValueKrw = value;
      expect(() => parseSummary(JSON.stringify(input))).toThrow();
    },
  );
  it('rejects invalid JSON numeric Infinity rather than confusing it with nullable values', () => {
    expect(() =>
      parseSummary(
        JSON.stringify(overviewSummary).replace('"totalValueKrw":null', '"totalValueKrw":Infinity'),
      ),
    ).toThrow();
  });
  it('rejects cross-snapshot, cross-user PnL, non-KRW PnL and malformed IDs', () => {
    const input = structuredClone(overviewSummary);
    input.performance!.portfolioSnapshotId = '2';
    expect(() => parseSummary(JSON.stringify(input))).toThrow();
    input.performance = null;
    input.botPnl!.scope.userId = '2';
    expect(() => parseSummary(JSON.stringify(input))).toThrow();
    input.botPnl!.scope.userId = '1';
    input.scope.quoteAsset = 'BTC';
    expect(() => parseSummary(JSON.stringify(input))).toThrow();
    input.botPnl = null;
    input.portfolio!.snapshotId = '9223372036854775808';
    expect(() => parseSummary(JSON.stringify(input))).toThrow();
  });
  it('rejects missing fields, invalid timestamps, unknown states and position ownership mismatch', () => {
    expect(() => parseSummary('{}')).toThrow();
    expect(() =>
      parseSummary(JSON.stringify({ ...overviewSummary, generatedAt: '2026-02-30T00:00:00Z' })),
    ).toThrow();
    expect(() =>
      parseSummary(
        JSON.stringify({
          ...overviewSummary,
          portfolio: { ...overviewSummary.portfolio, valuationStatus: 'FAKE' },
        }),
      ),
    ).toThrow();
    const input = structuredClone(overviewPositions);
    input.items[0].portfolioSnapshotId = '2';
    expect(() => parsePositions(JSON.stringify(input))).toThrow();
  });
  it('validates numeric counts without precision loss and inconsistent page metadata', () => {
    const raw = overviewFixtureResponse(new URL('http://local/performance?page=0&size=90'));
    expect(parsePerformance(raw).items).toHaveLength(90);
    expect(() => parsePerformance(raw.replace('"totalPages":2', '"totalPages":3'))).toThrow();
    const beyond =
      '{"scope":{"userId":"1","exchange":"UPBIT","quoteAsset":"KRW"},"items":[],"page":1000000,"size":1,"totalElements":0,"totalPages":0,"generatedAt":"2026-01-01T00:00:00Z"}';
    expect(parsePerformance(beyond).page).toBe(1000000);
  });
  it('rejects wrong recommendation quote, duplicate IDs, invalid ratios', () => {
    const raw = overviewFixtureResponse(new URL('http://local/recommendations'));
    expect(parseRecommendations(raw).items).toHaveLength(10);
    expect(() => parseRecommendations(raw.replaceAll('KRW-BTC', 'XKRW-BTC'))).toThrow();
    expect(() => parseRecommendations(raw.replace('0.9876', '1.01'))).toThrow();
    const input = JSON.parse(raw);
    input.items[1].id = input.items[0].id;
    expect(() => parseRecommendations(JSON.stringify(input))).toThrow();
  });
});
describe('exact decimal display', () => {
  it.each([
    ['12345678901234567890.1234567890', '12,345,678,901,234,567,890.123456789'],
    ['0.0000000001', '0.0000000001'],
    ['-12345.6789000000', '-12,345.6789'],
    ['0.0000000000', '0'],
    ['-0.000', '0'],
    [null, '미기록'],
  ])('formats %s exactly', (input, expected) => expect(decimalText(input)).toBe(expected));
  it('keeps percentage points separate from ratios and signs', () => {
    expect(percentText('1.2500000000')).toBe('1.25%');
    expect(ratioText('0.123456789')).toBe('12.3456789%');
    expect(ratioText('1.000')).toBe('100%');
    expect(moneyText(null)).toBe('미기록');
    expect(valueTone('-1.000')).toBe('negative');
    expect(valueTone('0.000')).toBe('');
  });
  it('converts explicit KST input and rejects invalid calendar dates', () => {
    expect(kstInstant('2026-01-01T09:00')).toBe('2026-01-01T00:00:00.000Z');
    expect(kstInstant('')).toBe('');
    expect(() => kstInstant('2026-02-30T09:00')).toThrow();
  });
});
describe('stored performance chart segments', () => {
  it('uses chronological order, breaks at PARTIAL and rebaseline, preserves source', () => {
    const data = [
      performanceRow(4),
      performanceRow(3, 'BASELINE'),
      performanceRow(2, 'PARTIAL'),
      performanceRow(1),
      performanceRow(0, 'BASELINE'),
    ];
    const { points, keys } = performanceSeries(data);
    expect(keys).toEqual(['s0', 's1']);
    expect(points[0].row.id).toBe('1000');
    expect(points[2]).not.toHaveProperty('s0');
    expect(points[3]).toHaveProperty('s1', 100);
    expect(data[0].id).toBe('1004');
    expect(points[4].row.performanceIndex).toBe('101.1234567890');
  });
  it('does not connect unknown continuity, null index or arbitrary page gaps', () => {
    const rows = [
      performanceRow(0),
      performanceRow(2),
      { ...performanceRow(3), performanceIndex: null },
      performanceRow(4),
    ];
    expect(performanceSeries(rows).keys).toHaveLength(3);
  });
});
