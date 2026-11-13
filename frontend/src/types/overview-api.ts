/** Overview wire values are strings; never coerce IDs or financial data to Number. */
export interface Scope {
  userId: string;
  exchange: string;
  quoteAsset: string;
}
export interface Portfolio {
  snapshotId: string;
  capturedAt: string;
  valuationStatus: 'COMPLETE' | 'PARTIAL';
  totalValueKrw: string | null;
  knownTotalValueKrw: string | null;
  pricedPositionsValueKrw: string | null;
  cashTotalKrw: string | null;
  unrealizedPnlKrw: string | null;
  positionCount: number;
  unpricedAssetCount: number;
  missingCostBasisCount: number;
}
export interface Performance {
  id: string;
  portfolioSnapshotId: string;
  periodStartAt: string | null;
  periodEndAt: string;
  returnMethod: string;
  periodReturnPercentage: string | null;
  cumulativeReturnPercentage: string | null;
  performanceIndex: string | null;
  drawdownIndex: string | null;
  drawdownPercentage: string | null;
  maxDrawdownPercentage: string | null;
  performanceStatus: 'BASELINE' | 'COMPLETE' | 'PARTIAL';
  safeReason: string | null;
  calculatedAt: string;
  endValueKrw: string | null;
}
export interface BotPnl {
  scope: { userId: string; exchange: string; amountCurrency: 'KRW' };
  accountingStatus: 'COMPLETE' | 'PARTIAL';
  recognizedRealizedPnlKrw: string | null;
  recognizedRealizedReturnPercentage: string | null;
  totalFeesKrw: string | null;
  processedOrderCount: number;
  fullyMatchedSellCount: number;
  partiallyMatchedSellCount: number;
  unmatchedSellCount: number;
  incompleteOrderCount: number;
  calculatedAt: string;
  sourceLastUpdatedAt: string | null;
}
export interface Position {
  id: string;
  portfolioSnapshotId: string;
  currency: string;
  market: string | null;
  availableQuantity: string | null;
  lockedQuantity: string | null;
  totalQuantity: string | null;
  avgBuyPrice: string | null;
  markPrice: string | null;
  marketValueKrw: string | null;
  estimatedCostBasisKrw: string | null;
  unrealizedPnlKrw: string | null;
  unrealizedPnlPercentage: string | null;
  valuationStatus: 'PRICED' | 'UNPRICED' | 'EXCLUDED';
  priceSource: string | null;
}
export type Action = 'BUY' | 'SELL' | 'HOLD';
export interface Recommendation {
  id: string;
  analysisRunId: string;
  market: string;
  action: Action;
  confidence: string | null;
  tradeRatio: string | null;
  aiModel: string | null;
  status: string;
  createdAt: string;
}
export interface Envelope {
  scope: Scope;
  generatedAt: string;
}
export interface Summary extends Envelope {
  portfolio: Portfolio | null;
  performance: Performance | null;
  botPnl: BotPnl | null;
}
export interface Positions extends Envelope {
  portfolio: Portfolio | null;
  items: Position[];
}
export interface Page<T> extends Envelope {
  items: T[];
  page: number;
  size: number;
  totalElements: string;
  totalPages: string;
}
export interface PerformanceFilter {
  from: string;
  to: string;
  page: number;
  size: number;
}
export interface RecommendationFilter {
  action: '' | Action;
  page: number;
  size: number;
}
