import type { Summary, Positions, Performance, Recommendation } from '../types/overview-api';
export const overviewBigId = '9007199254740993';
export const overviewScope = { userId: '1', exchange: 'UPBIT', quoteAsset: 'KRW' };
export const observedAt = '2026-01-04T00:00:00Z';
export const overviewSummary: Summary = {
  scope: overviewScope,
  generatedAt: '2026-01-05T00:00:00Z',
  portfolio: {
    snapshotId: overviewBigId,
    capturedAt: observedAt,
    valuationStatus: 'PARTIAL',
    totalValueKrw: null,
    knownTotalValueKrw: '12345678901234567890.1234567890',
    pricedPositionsValueKrw: '13000.0000000000',
    cashTotalKrw: '1000.0000000000',
    unrealizedPnlKrw: null,
    positionCount: 3,
    unpricedAssetCount: 1,
    missingCostBasisCount: 1,
  },
  performance: {
    id: overviewBigId,
    portfolioSnapshotId: overviewBigId,
    periodStartAt: '2026-01-03T00:00:00Z',
    periodEndAt: observedAt,
    returnMethod: 'MODIFIED_DIETZ',
    periodReturnPercentage: null,
    cumulativeReturnPercentage: null,
    performanceIndex: null,
    drawdownIndex: null,
    drawdownPercentage: null,
    maxDrawdownPercentage: null,
    performanceStatus: 'PARTIAL',
    safeReason: 'UNPRICED_ASSET',
    calculatedAt: '2026-01-04T01:00:00Z',
    endValueKrw: null,
  },
  botPnl: {
    scope: { userId: '1', exchange: 'UPBIT', amountCurrency: 'KRW' },
    accountingStatus: 'PARTIAL',
    recognizedRealizedPnlKrw: '-12345.6789000000',
    recognizedRealizedReturnPercentage: '-1.2345678901',
    totalFeesKrw: '12.3000000000',
    processedOrderCount: 5,
    fullyMatchedSellCount: 1,
    partiallyMatchedSellCount: 1,
    unmatchedSellCount: 1,
    incompleteOrderCount: 2,
    calculatedAt: '2026-01-01T02:00:00Z',
    sourceLastUpdatedAt: '2026-01-01T00:00:00Z',
  },
};
export const overviewPositions: Positions = {
  scope: overviewScope,
  generatedAt: overviewSummary.generatedAt,
  portfolio: overviewSummary.portfolio,
  items: ['BTC', 'APENFT', 'QI'].map((currency, i) => ({
    id: String(3001 + i),
    portfolioSnapshotId: overviewBigId,
    currency,
    market: i === 0 ? 'KRW-BTC' : null,
    availableQuantity: '0.0000000001',
    lockedQuantity: '0.0000000000',
    totalQuantity: '0.0000000001',
    avgBuyPrice: i === 0 ? '100.0000000000' : null,
    markPrice: i === 0 ? '1000.0000000000' : null,
    marketValueKrw: i === 0 ? '12000.0000000000' : null,
    estimatedCostBasisKrw: i === 0 ? '10000.0000000000' : null,
    unrealizedPnlKrw: i === 0 ? '2000.0000000000' : null,
    unrealizedPnlPercentage: i === 0 ? '20.0000000000' : null,
    valuationStatus: i === 0 ? 'PRICED' : i === 1 ? 'UNPRICED' : 'EXCLUDED',
    priceSource: i === 0 ? 'MARKET_SNAPSHOT' : i === 1 ? 'UNAVAILABLE' : 'POLICY_EXCLUDED',
  })),
};
export function performanceRow(
  n: number,
  status: Performance['performanceStatus'] = 'COMPLETE',
): Performance {
  const at = new Date(Date.UTC(2026, 0, 1 + n)).toISOString();
  return {
    ...overviewSummary.performance!,
    id: String(1000 + n),
    portfolioSnapshotId: String(1000 + n),
    periodStartAt: status === 'BASELINE' ? null : new Date(Date.UTC(2026, 0, n)).toISOString(),
    periodEndAt: at,
    calculatedAt: at,
    performanceStatus: status,
    performanceIndex:
      status === 'PARTIAL' ? null : status === 'BASELINE' ? '100.0000000000' : '101.1234567890',
    cumulativeReturnPercentage:
      status === 'PARTIAL' ? null : status === 'BASELINE' ? '0.0000000000' : '1.1234567890',
    periodReturnPercentage: status === 'COMPLETE' ? '1.1234567890' : null,
    safeReason:
      status === 'PARTIAL'
        ? 'UNPRICED_ASSET'
        : status === 'BASELINE'
          ? 'REBASELINE_AFTER_PARTIAL_GAP'
          : null,
    drawdownIndex: status === 'PARTIAL' ? null : '-3.0000000000',
    drawdownPercentage: status === 'PARTIAL' ? null : '-2.1234567890',
    maxDrawdownPercentage: status === 'PARTIAL' ? null : '-4.0000000000',
  };
}
const history = Array.from({ length: 95 }, (_, i) =>
  performanceRow(i, i === 92 ? 'PARTIAL' : i === 0 || i === 93 ? 'BASELINE' : 'COMPLETE'),
).reverse();
const recommendations: Recommendation[] = Array.from({ length: 22 }, (_, i) => ({
  id: (BigInt(overviewBigId) + BigInt(i)).toString(),
  analysisRunId: (BigInt(overviewBigId) + 100n + BigInt(i)).toString(),
  market: i % 2 ? 'KRW-ETH' : 'KRW-BTC',
  action: (['BUY', 'SELL', 'HOLD'] as const)[i % 3],
  confidence: '0.9876',
  tradeRatio: i % 3 === 2 ? null : '0.123456789',
  aiModel: 'fixture-model',
  status: i % 3 === 2 ? 'CREATED' : 'APPROVED',
  createdAt: observedAt,
})).reverse();
export function overviewFixtureResponse(url: URL): string {
  if (url.pathname.endsWith('/summary')) return JSON.stringify(overviewSummary);
  if (url.pathname.endsWith('/positions')) return JSON.stringify(overviewPositions);
  const isPerformance = url.pathname.endsWith('/performance');
  const page = Number(url.searchParams.get('page') ?? 0),
    size = Number(url.searchParams.get('size') ?? (isPerformance ? 90 : 10));
  const from = url.searchParams.get('from'),
    to = url.searchParams.get('to'),
    action = url.searchParams.get('action');
  const rows = isPerformance
    ? history.filter(
        (p) =>
          (!from || Date.parse(p.periodEndAt) >= Date.parse(from)) &&
          (!to || Date.parse(p.periodEndAt) <= Date.parse(to)),
      )
    : recommendations.filter((r) => !action || r.action === action);
  return JSON.stringify({
    scope: overviewScope,
    items: rows.slice(page * size, (page + 1) * size),
    page,
    size,
    totalElements: rows.length,
    totalPages: Math.ceil(rows.length / size),
    generatedAt: overviewSummary.generatedAt,
  });
}
