import { parse, isLosslessNumber, compareNumber } from 'lossless-json';
import { timestamp, validId } from './research-api-parser';
import type {
  Scope,
  Portfolio,
  Performance,
  BotPnl,
  Position,
  Recommendation,
  Summary,
  Positions,
  Page,
} from '@/types/overview-api';

export class InvalidOverviewData extends Error {
  constructor() {
    super('Invalid Overview response');
  }
}
function invalid(): never {
  throw new InvalidOverviewData();
}
function object(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) && !isLosslessNumber(v)
    ? (v as Record<string, unknown>)
    : invalid();
}
function text(v: unknown): string {
  return typeof v === 'string' && v.length > 0 && v.length <= 1024 ? v : invalid();
}
function id(v: unknown): string {
  return typeof v === 'string' && validId(v) ? v : invalid();
}
function nullable<T>(v: unknown, read: (x: unknown) => T): T | null {
  return v === null ? null : read(v);
}
function choice<T extends string>(v: unknown, values: readonly T[]): T {
  return typeof v === 'string' && values.includes(v as T) ? (v as T) : invalid();
}
function count(v: unknown): string {
  if (
    !isLosslessNumber(v) ||
    !/^(0|[1-9]\d{0,18})$/.test(v.value) ||
    BigInt(v.value) > 9223372036854775807n
  )
    return invalid();
  return v.value;
}
function integer(v: unknown, min = 0, max = 2147483647): number {
  const n = BigInt(count(v));
  return n >= BigInt(min) && n <= BigInt(max) ? Number(n) : invalid();
}
function decimal(v: unknown): string {
  if (
    typeof v !== 'string' ||
    !/^-?(0|[1-9]\d{0,29})(\.\d{1,10})?$/.test(v) ||
    v.replace(/[-.]/g, '').length > 30
  )
    return invalid();
  return v;
}
function ratio(v: unknown): string {
  const d = decimal(v);
  return compareNumber(d, '0') >= 0 && compareNumber(d, '1') <= 0 ? d : invalid();
}
function scope(v: unknown): Scope {
  const o = object(v);
  return { userId: id(o.userId), exchange: text(o.exchange), quoteAsset: text(o.quoteAsset) };
}
export function sameScope(a: Scope, b: Scope): boolean {
  return a.userId === b.userId && a.exchange === b.exchange && a.quoteAsset === b.quoteAsset;
}
function list<T extends { id: string }>(v: unknown, read: (x: unknown) => T): T[] {
  if (!Array.isArray(v)) return invalid();
  const rows = v.map(read);
  if (new Set(rows.map((r) => r.id)).size !== rows.length) return invalid();
  return rows;
}
function decode<T>(raw: string, read: (v: unknown) => T): T {
  try {
    return read(parse(raw));
  } catch {
    return invalid();
  }
}
function portfolio(v: unknown): Portfolio {
  const o = object(v);
  return {
    snapshotId: id(o.snapshotId),
    capturedAt: timestamp(o.capturedAt),
    valuationStatus: choice(o.valuationStatus, ['COMPLETE', 'PARTIAL']),
    totalValueKrw: nullable(o.totalValueKrw, decimal),
    knownTotalValueKrw: nullable(o.knownTotalValueKrw, decimal),
    pricedPositionsValueKrw: nullable(o.pricedPositionsValueKrw, decimal),
    cashTotalKrw: nullable(o.cashTotalKrw, decimal),
    unrealizedPnlKrw: nullable(o.unrealizedPnlKrw, decimal),
    positionCount: integer(o.positionCount),
    unpricedAssetCount: integer(o.unpricedAssetCount),
    missingCostBasisCount: integer(o.missingCostBasisCount),
  };
}
function performance(v: unknown): Performance {
  const o = object(v);
  return {
    id: id(o.id),
    portfolioSnapshotId: id(o.portfolioSnapshotId),
    periodStartAt: nullable(o.periodStartAt, timestamp),
    periodEndAt: timestamp(o.periodEndAt),
    returnMethod: text(o.returnMethod),
    periodReturnPercentage: nullable(o.periodReturnPercentage, decimal),
    cumulativeReturnPercentage: nullable(o.cumulativeReturnPercentage, decimal),
    performanceIndex: nullable(o.performanceIndex, decimal),
    drawdownIndex: nullable(o.drawdownIndex, decimal),
    drawdownPercentage: nullable(o.drawdownPercentage, decimal),
    maxDrawdownPercentage: nullable(o.maxDrawdownPercentage, decimal),
    performanceStatus: choice(o.performanceStatus, ['BASELINE', 'COMPLETE', 'PARTIAL']),
    safeReason: nullable(o.safeReason, text),
    calculatedAt: timestamp(o.calculatedAt),
    endValueKrw: nullable(o.endValueKrw, decimal),
  };
}
function botPnl(v: unknown): BotPnl {
  const o = object(v),
    s = object(o.scope);
  return {
    scope: {
      userId: id(s.userId),
      exchange: text(s.exchange),
      amountCurrency: choice(s.amountCurrency, ['KRW']),
    },
    accountingStatus: choice(o.accountingStatus, ['COMPLETE', 'PARTIAL']),
    recognizedRealizedPnlKrw: nullable(o.recognizedRealizedPnlKrw, decimal),
    recognizedRealizedReturnPercentage: nullable(o.recognizedRealizedReturnPercentage, decimal),
    totalFeesKrw: nullable(o.totalFeesKrw, decimal),
    processedOrderCount: integer(o.processedOrderCount),
    fullyMatchedSellCount: integer(o.fullyMatchedSellCount),
    partiallyMatchedSellCount: integer(o.partiallyMatchedSellCount),
    unmatchedSellCount: integer(o.unmatchedSellCount),
    incompleteOrderCount: integer(o.incompleteOrderCount),
    calculatedAt: timestamp(o.calculatedAt),
    sourceLastUpdatedAt: nullable(o.sourceLastUpdatedAt, timestamp),
  };
}
export function parseSummary(raw: string): Summary {
  return decode(raw, (v) => {
    const o = object(v),
      result = {
        scope: scope(o.scope),
        portfolio: nullable(o.portfolio, portfolio),
        performance: nullable(o.performance, performance),
        botPnl: nullable(o.botPnl, botPnl),
        generatedAt: timestamp(o.generatedAt),
      };
    if (
      result.performance &&
      result.performance.portfolioSnapshotId !== result.portfolio?.snapshotId
    )
      return invalid();
    if (
      result.botPnl &&
      (result.scope.quoteAsset !== 'KRW' ||
        result.botPnl.scope.userId !== result.scope.userId ||
        result.botPnl.scope.exchange !== result.scope.exchange)
    )
      return invalid();
    return result;
  });
}
function position(v: unknown): Position {
  const o = object(v);
  return {
    id: id(o.id),
    portfolioSnapshotId: id(o.portfolioSnapshotId),
    currency: text(o.currency),
    market: nullable(o.market, text),
    availableQuantity: nullable(o.availableQuantity, decimal),
    lockedQuantity: nullable(o.lockedQuantity, decimal),
    totalQuantity: nullable(o.totalQuantity, decimal),
    avgBuyPrice: nullable(o.avgBuyPrice, decimal),
    markPrice: nullable(o.markPrice, decimal),
    marketValueKrw: nullable(o.marketValueKrw, decimal),
    estimatedCostBasisKrw: nullable(o.estimatedCostBasisKrw, decimal),
    unrealizedPnlKrw: nullable(o.unrealizedPnlKrw, decimal),
    unrealizedPnlPercentage: nullable(o.unrealizedPnlPercentage, decimal),
    valuationStatus: choice(o.valuationStatus, ['PRICED', 'UNPRICED', 'EXCLUDED']),
    priceSource: nullable(o.priceSource, text),
  };
}
export function parsePositions(raw: string): Positions {
  return decode(raw, (v) => {
    const o = object(v),
      p = nullable(o.portfolio, portfolio),
      items = list(o.items, position);
    if (items.length > 1000 || items.some((i) => i.portfolioSnapshotId !== p?.snapshotId))
      return invalid();
    return { scope: scope(o.scope), portfolio: p, items, generatedAt: timestamp(o.generatedAt) };
  });
}
function page<T extends { id: string }>(
  v: unknown,
  read: (v: unknown) => T,
  maximum: number,
): Page<T> {
  const o = object(v),
    size = integer(o.size, 1, maximum),
    number = integer(o.page),
    totalElements = count(o.totalElements),
    totalPages = count(o.totalPages),
    items = list(o.items, read);
  const remaining = BigInt(totalElements) - BigInt(number) * BigInt(size);
  const expectedLength = remaining <= 0n ? 0 : remaining >= BigInt(size) ? size : Number(remaining);
  if (
    number * size > 1_000_000 ||
    BigInt(totalPages) !== (BigInt(totalElements) + BigInt(size) - 1n) / BigInt(size) ||
    items.length !== expectedLength
  )
    return invalid();
  return {
    scope: scope(o.scope),
    items,
    page: number,
    size,
    totalElements,
    totalPages,
    generatedAt: timestamp(o.generatedAt),
  };
}
export const parsePerformance = (raw: string): Page<Performance> =>
  decode(raw, (v) => page(v, performance, 200));
export const parseRecommendations = (raw: string): Page<Recommendation> =>
  decode(raw, (v) => {
    const result = page(
      v,
      (x) => {
        const o = object(x);
        return {
          id: id(o.id),
          analysisRunId: id(o.analysisRunId),
          market: text(o.market),
          action: choice(o.action, ['BUY', 'SELL', 'HOLD']),
          confidence: nullable(o.confidence, ratio),
          tradeRatio: nullable(o.tradeRatio, ratio),
          aiModel: nullable(o.aiModel, text),
          status: text(o.status),
          createdAt: timestamp(o.createdAt),
        };
      },
      50,
    );
    if (
      result.items.some(
        (r) =>
          result.scope.exchange !== 'UPBIT' ||
          !r.market.startsWith(result.scope.quoteAsset + '-') ||
          r.market.length <= result.scope.quoteAsset.length + 1,
      )
    )
      return invalid();
    return result;
  });
