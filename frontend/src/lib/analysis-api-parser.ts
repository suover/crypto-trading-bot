import { parse, isLosslessNumber, compareNumber } from 'lossless-json';
import { timestamp, validId } from './research-api-parser';
import type {
  Recommendation,
  Analysis,
  Universe,
  Candidate,
  Approval,
  Attempt,
  Order,
  Detail,
  Candidates,
  Page,
  Scope,
  Timeframe,
} from '@/types/analysis-api';
import { metrics } from '@/types/analysis-api';
export class InvalidAnalysisData extends Error {
  constructor() {
    super('Invalid AI Trade Analysis response');
  }
}
function invalid(): never {
  throw new InvalidAnalysisData();
}
function object(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) && !isLosslessNumber(v)
    ? (v as Record<string, unknown>)
    : invalid();
}
function text(v: unknown): string {
  return typeof v === 'string' ? v : invalid();
}
function id(v: unknown): string {
  return typeof v === 'string' && validId(v) ? v : invalid();
}
function nullable<T>(v: unknown, read: (v: unknown) => T): T | null {
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
// Includes the backend's expanded indicator precision; never passes money through Number.
function decimal(v: unknown): string {
  return typeof v === 'string' && v.length <= 285 && /^-?(0|[1-9]\d*)(\.\d+)?$/.test(v)
    ? v
    : invalid();
}
function ratio(v: unknown): string {
  const d = decimal(v);
  return compareNumber(d, '0') >= 0 && compareNumber(d, '1') <= 0 ? d : invalid();
}
function bool(v: unknown): boolean {
  return typeof v === 'boolean' ? v : invalid();
}
function scope(v: unknown): Scope {
  const o = object(v);
  return { userId: id(o.userId), exchange: text(o.exchange), quoteAsset: text(o.quoteAsset) };
}
function array<T>(v: unknown, read: (v: unknown) => T, max: number): T[] {
  if (!Array.isArray(v) || v.length > max) return invalid();
  return v.map(read);
}
function list<T extends { id: string }>(v: unknown, read: (v: unknown) => T, max: number): T[] {
  const rows = array(v, read, max);
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
function marketMatches(market: string, s: Scope) {
  return (
    s.exchange === 'UPBIT' &&
    market.startsWith(s.quoteAsset + '-') &&
    market.length > s.quoteAsset.length + 1
  );
}
function recommendation(v: unknown): Recommendation {
  const o = object(v);
  return {
    id: id(o.id),
    analysisRunId: id(o.analysisRunId),
    market: text(o.market),
    action: choice(o.action, ['BUY', 'SELL', 'HOLD']),
    tradeRatio: nullable(o.tradeRatio, ratio),
    confidence: nullable(o.confidence, ratio),
    aiModel: nullable(o.aiModel, text),
    status: text(o.status),
    createdAt: timestamp(o.createdAt),
    updatedAt: timestamp(o.updatedAt),
  };
}
function analysis(v: unknown): Analysis {
  const o = object(v);
  return {
    id: id(o.id),
    pipelineRunId: nullable(o.pipelineRunId, text),
    runType: text(o.runType),
    tradingMode: text(o.tradingMode),
    status: text(o.status),
    startedAt: timestamp(o.startedAt),
    finishedAt: nullable(o.finishedAt, timestamp),
  };
}
function universe(v: unknown): Universe {
  const o = object(v),
    result: Universe = {
      status: choice(o.status, ['VERIFIED', 'NO_REFERENCE', 'INVALID_REFERENCE']),
      analysisRunId: nullable(o.analysisRunId, id),
      selectedCandidateId: nullable(o.selectedCandidateId, id),
    };
  if (
    result.status === 'VERIFIED'
      ? !result.analysisRunId || !result.selectedCandidateId
      : result.analysisRunId !== null || result.selectedCandidateId !== null
  )
    return invalid();
  return result;
}
function timeframe(v: unknown): Timeframe {
  const o = object(v),
    indicators = object(o.indicators);
  const result: Timeframe = {
    timeframe: choice(o.timeframe, ['15m', '1h', '4h', '1d']),
    dataQuality: nullable(o.dataQuality, (v) =>
      choice(v, ['SUFFICIENT', 'INSUFFICIENT', 'UNAVAILABLE']),
    ),
    candleCount: nullable(o.candleCount, integer),
    trendLabel: nullable(o.trendLabel, (v) =>
      choice(v, ['상승 우위', '하락 우위', '관망', '판단 보류', '과열 주의', '과매도 주의']),
    ),
    indicators: {},
    invalidFields: array(
      o.invalidFields,
      (v) => choice(v, ['timeframe', 'data_quality', 'candle_count', 'trend_label', ...metrics]),
      19,
    ),
  };
  for (const metric of metrics)
    if (metric in indicators) result.indicators[metric] = nullable(indicators[metric], decimal);
  return result;
}
function candidate(v: unknown): Candidate {
  const o = object(v),
    f = object(o.features),
    timeframes = array(f.timeframes, timeframe, 4);
  if (new Set(timeframes.map((t) => t.timeframe)).size !== timeframes.length) return invalid();
  return {
    id: id(o.id),
    universeAnalysisRunId: id(o.universeAnalysisRunId),
    market: text(o.market),
    baseAsset: text(o.baseAsset),
    quoteAsset: text(o.quoteAsset),
    rank: nullable(o.rank, (v) => integer(v, 1)),
    score: nullable(o.score, decimal),
    selectionSource: text(o.selectionSource),
    buyEligible: nullable(o.buyEligible, bool),
    sellEligible: nullable(o.sellEligible, bool),
    quoteTradeValue24h: nullable(o.quoteTradeValue24h, decimal),
    createdAt: timestamp(o.createdAt),
    features: {
      projectionStatus: choice(f.projectionStatus, ['COMPLETE', 'PARTIAL', 'UNAVAILABLE']),
      dataQuality: nullable(f.dataQuality, (v) =>
        choice(v, ['SUFFICIENT', 'PARTIAL', 'INSUFFICIENT', 'UNAVAILABLE']),
      ),
      timeframes,
    },
  };
}
function approval(v: unknown): Approval {
  const o = object(v);
  return {
    id: id(o.id),
    status: text(o.status),
    expiresAt: timestamp(o.expiresAt),
    approvedAt: nullable(o.approvedAt, timestamp),
    rejectedAt: nullable(o.rejectedAt, timestamp),
    createdAt: timestamp(o.createdAt),
    updatedAt: timestamp(o.updatedAt),
  };
}
function attempt(v: unknown): Attempt {
  const o = object(v);
  return {
    id: id(o.id),
    approvalRequestId: id(o.approvalRequestId),
    orderLogId: nullable(o.orderLogId, id),
    tradingMode: text(o.tradingMode),
    attemptNumber: integer(o.attemptNumber, 1),
    status: text(o.status),
    errorCode: nullable(o.errorCode, text),
    attemptedAt: timestamp(o.attemptedAt),
    nextRetryAt: nullable(o.nextRetryAt, timestamp),
  };
}
function order(v: unknown): Order {
  const o = object(v);
  return {
    id: id(o.id),
    approvalRequestId: nullable(o.approvalRequestId, id),
    tradingMode: text(o.tradingMode),
    market: text(o.market),
    side: text(o.side),
    orderType: text(o.orderType),
    status: text(o.status),
    amountKrw: nullable(o.amountKrw, decimal),
    quantity: nullable(o.quantity, decimal),
    price: nullable(o.price, decimal),
    executedQuantity: nullable(o.executedQuantity, decimal),
    executedFundsKrw: nullable(o.executedFundsKrw, decimal),
    averageExecutionPrice: nullable(o.averageExecutionPrice, decimal),
    paidFee: nullable(o.paidFee, decimal),
    remainingQuantity: nullable(o.remainingQuantity, decimal),
    tradesCount: nullable(o.tradesCount, integer),
    executionSyncedAt: nullable(o.executionSyncedAt, timestamp),
    createdAt: timestamp(o.createdAt),
    updatedAt: timestamp(o.updatedAt),
  };
}
function page<T extends { id: string }>(v: unknown, read: (v: unknown) => T): Page<T> {
  const o = object(v),
    size = integer(o.size, 1, 100),
    number = integer(o.page),
    totalElements = count(o.totalElements),
    totalPages = count(o.totalPages),
    items = list(o.items, read, size);
  const remaining = BigInt(totalElements) - BigInt(number) * BigInt(size);
  if (
    number * size > 1_000_000 ||
    BigInt(totalPages) !== (BigInt(totalElements) + BigInt(size) - 1n) / BigInt(size) ||
    items.length !== (remaining <= 0n ? 0 : remaining >= BigInt(size) ? size : Number(remaining))
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
export const parseAnalysisRecommendations = (raw: string): Page<Recommendation> =>
  decode(raw, (v) => {
    const p = page(v, recommendation);
    if (p.items.some((r) => !marketMatches(r.market, p.scope))) return invalid();
    return p;
  });
function validCandidate(c: Candidate, s: Scope, u: Universe) {
  return (
    c.universeAnalysisRunId === u.analysisRunId &&
    c.quoteAsset === s.quoteAsset &&
    marketMatches(c.market, s)
  );
}
export const parseAnalysisCandidates = (raw: string): Candidates =>
  decode(raw, (v) => {
    const p = page(v, candidate),
      u = universe(object(v).universe);
    if (
      u.status !== 'VERIFIED'
        ? p.totalElements !== '0'
        : p.items.some((c) => !validCandidate(c, p.scope, u))
    )
      return invalid();
    return { ...p, universe: u };
  });
export const parseAnalysisDetail = (raw: string): Detail =>
  decode(raw, (v) => {
    const o = object(v),
      d: Detail = {
        scope: scope(o.scope),
        recommendation: recommendation(o.recommendation),
        analysis: analysis(o.analysis),
        finalDecisionReason: text(o.finalDecisionReason),
        recommendedAmountKrw: nullable(o.recommendedAmountKrw, decimal),
        recommendedQuantity: nullable(o.recommendedQuantity, decimal),
        universe: universe(o.universe),
        selectedCandidate: nullable(o.selectedCandidate, candidate),
        approvals: list(o.approvals, approval, 1000),
        executionAttempts: list(o.executionAttempts, attempt, 1000),
        order: nullable(o.order, order),
        generatedAt: timestamp(o.generatedAt),
      };
    if (
      d.analysis.id !== d.recommendation.analysisRunId ||
      !marketMatches(d.recommendation.market, d.scope)
    )
      return invalid();
    const c = d.selectedCandidate;
    if (
      d.universe.status === 'VERIFIED'
        ? !c ||
          c.id !== d.universe.selectedCandidateId ||
          c.market !== d.recommendation.market ||
          !validCandidate(c, d.scope, d.universe) ||
          !d.analysis.pipelineRunId?.trim()
        : c !== null
    )
      return invalid();
    const approvalIds = new Set(d.approvals.map((a) => a.id));
    if (
      d.order &&
      (d.order.market !== d.recommendation.market ||
        (d.order.approvalRequestId !== null && !approvalIds.has(d.order.approvalRequestId)))
    )
      return invalid();
    if (
      d.executionAttempts.some(
        (a) =>
          !approvalIds.has(a.approvalRequestId) ||
          (a.orderLogId !== null &&
            (a.orderLogId !== d.order?.id ||
              a.approvalRequestId !== d.order.approvalRequestId ||
              a.tradingMode !== d.order.tradingMode)),
      )
    )
      return invalid();
    return d;
  });
