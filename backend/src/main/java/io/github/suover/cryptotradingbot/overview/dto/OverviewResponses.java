package io.github.suover.cryptotradingbot.overview.dto;

import io.github.suover.cryptotradingbot.overview.entity.*;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

// Overview wire contract is string-safe; existing Research serializers are unchanged.
public final class OverviewResponses {
    private OverviewResponses() {}
    private static String decimal(BigDecimal value) { return value == null ? null : value.toPlainString(); }
    private static String id(Long value) { return value == null ? null : value.toString(); }

    public record Scope(String userId, String exchange, String quoteAsset) {}
    public record Summary(Scope scope, Portfolio portfolio, Performance performance, BotPnl botPnl,
                          Instant generatedAt) {}
    public record Positions(Scope scope, Portfolio portfolio, List<Position> items, Instant generatedAt) {
        public Positions { items = List.copyOf(items); }
    }
    public record Page<T>(Scope scope, List<T> items, int page, int size, long totalElements,
                          long totalPages, Instant generatedAt) {
        public Page { items = List.copyOf(items); }
    }
    public record Portfolio(String snapshotId, Instant capturedAt, String valuationStatus,
                            String totalValueKrw, String knownTotalValueKrw, String pricedPositionsValueKrw,
                            String cashTotalKrw, String unrealizedPnlKrw, Integer positionCount,
                            Integer unpricedAssetCount, Integer missingCostBasisCount) {
        public static Portfolio from(PortfolioSnapshot s) {
            return new Portfolio(id(s.getId()), s.getCapturedAt(), s.getValuationStatus(),
                    decimal(s.getTotalValueKrw()), decimal(s.getKnownTotalValueKrw()),
                    decimal(s.getPricedPositionsValueKrw()), decimal(s.getCashTotalKrw()),
                    decimal(s.getUnrealizedPnlKrw()), s.getPositionCount(),
                    s.getUnpricedAssetCount(), s.getMissingCostBasisCount());
        }
    }
    public record Position(String id, String portfolioSnapshotId, String currency, String market,
                           String availableQuantity, String lockedQuantity, String totalQuantity,
                           String avgBuyPrice, String markPrice, String marketValueKrw,
                           String estimatedCostBasisKrw, String unrealizedPnlKrw,
                           String unrealizedPnlPercentage, String valuationStatus, String priceSource) {
        public static Position from(PortfolioPositionSnapshot p) {
            return new Position(OverviewResponses.id(p.getId()), OverviewResponses.id(p.getPortfolioSnapshotId()),
                    p.getCurrency(), p.getMarket(), decimal(p.getAvailableQuantity()),
                    decimal(p.getLockedQuantity()), decimal(p.getTotalQuantity()), decimal(p.getAvgBuyPrice()),
                    decimal(p.getMarkPrice()), decimal(p.getMarketValueKrw()), decimal(p.getEstimatedCostBasisKrw()),
                    decimal(p.getUnrealizedPnlKrw()), decimal(p.getUnrealizedPnlPercentage()),
                    p.getValuationStatus(), p.getPriceSource());
        }
    }
    public record Performance(String id, String portfolioSnapshotId, Instant periodStartAt,
                              Instant periodEndAt, String returnMethod, String periodReturnPercentage,
                              String cumulativeReturnPercentage, String performanceIndex, String drawdownIndex,
                              String drawdownPercentage, String maxDrawdownPercentage,
                              String performanceStatus, String safeReason, Instant calculatedAt, String endValueKrw) {
        public static Performance from(PortfolioPerformanceSnapshot p) {
            return new Performance(OverviewResponses.id(p.getId()), OverviewResponses.id(p.getPortfolioSnapshotId()),
                    p.getPeriodStartAt(), p.getPeriodEndAt(), p.getReturnMethod(),
                    decimal(p.getPeriodReturnPercentage()), decimal(p.getCumulativeReturnPercentage()),
                    decimal(p.getPerformanceIndex()), decimal(p.getDrawdownIndex()),
                    decimal(p.getDrawdownPercentage()), decimal(p.getMaxDrawdownPercentage()),
                    p.getPerformanceStatus(), p.getSafeReason(), p.getCalculatedAt(), decimal(p.getEndValueKrw()));
        }
    }
    public record BotPnlScope(String userId, String exchange, String amountCurrency) {}
    public record BotPnl(BotPnlScope scope, String accountingStatus, String recognizedRealizedPnlKrw,
                         String recognizedRealizedReturnPercentage, String totalFeesKrw,
                         Integer processedOrderCount, Integer fullyMatchedSellCount,
                         Integer partiallyMatchedSellCount, Integer unmatchedSellCount,
                         Integer incompleteOrderCount, Instant calculatedAt, Instant sourceLastUpdatedAt) {
        public static BotPnl from(BotTradingPnlSummary b) {
            return new BotPnl(new BotPnlScope(id(b.getUserId()), b.getExchange(), "KRW"),
                    b.getAccountingStatus(), decimal(b.getRecognizedRealizedPnlKrw()),
                    decimal(b.getRecognizedRealizedReturnPercentage()), decimal(b.getTotalFeesKrw()),
                    b.getProcessedOrderCount(), b.getFullyMatchedSellCount(), b.getPartiallyMatchedSellCount(),
                    b.getUnmatchedSellCount(), b.getIncompleteOrderCount(), b.getCalculatedAt(), b.getSourceLastUpdatedAt());
        }
    }
    public record Recommendation(String id, String analysisRunId, String market, String action,
                                 String confidence, String tradeRatio, String aiModel, String status, Instant createdAt) {
        public static Recommendation from(TradeRecommendation r) {
            return new Recommendation(OverviewResponses.id(r.getId()), OverviewResponses.id(r.getAnalysisRunId()),
                    r.getMarket(), r.getAction(), decimal(r.getConfidence()), decimal(r.getTradeRatio()),
                    r.getAiModel(), r.getStatus(), r.getCreatedAt());
        }
    }
}
