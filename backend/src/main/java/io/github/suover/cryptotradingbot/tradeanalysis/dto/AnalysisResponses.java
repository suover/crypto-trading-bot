package io.github.suover.cryptotradingbot.tradeanalysis.dto;

import io.github.suover.cryptotradingbot.tradeanalysis.entity.*;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;

public final class AnalysisResponses {
    private AnalysisResponses() {}
    private static String id(Long value) { return value == null ? null : value.toString(); }
    private static String decimal(BigDecimal value) { return value == null ? null : value.toPlainString(); }
    public record Scope(String userId, String exchange, String quoteAsset) {}
    public record Page<T>(Scope scope, List<T> items, int page, int size, long totalElements,
                          long totalPages, Instant generatedAt) {
        public Page { items = List.copyOf(items); }
    }
    public record Recommendation(String id, String analysisRunId, String market, String action,
                                 String tradeRatio, String confidence, String aiModel, String status,
                                 Instant createdAt, Instant updatedAt) {
        public static Recommendation from(RecommendationRecord r) {
            return new Recommendation(AnalysisResponses.id(r.getId()), AnalysisResponses.id(r.getAnalysisRunId()),
                    r.getMarket(), r.getAction(), decimal(r.getTradeRatio()), decimal(r.getConfidence()),
                    r.getAiModel(), r.getStatus(), r.getCreatedAt(), r.getUpdatedAt());
        }
    }
    public record Analysis(String id, String pipelineRunId, String runType, String tradingMode,
                           String status, Instant startedAt, Instant finishedAt) {
        public static Analysis from(AnalysisRecord a) {
            return new Analysis(AnalysisResponses.id(a.getId()), a.getPipelineRunId(), a.getRunType(),
                    a.getTradingMode(), a.getStatus(), a.getStartedAt(), a.getFinishedAt());
        }
    }
    // NO_REFERENCE is normal for historical/static records. INVALID_REFERENCE never falls back to latest data.
    public record Universe(String status, String analysisRunId, String selectedCandidateId) {}
    public record FeatureSummary(String projectionStatus, String dataQuality, List<Timeframe> timeframes) {
        public FeatureSummary { timeframes = List.copyOf(timeframes); }
    }
    public record Timeframe(String timeframe, String dataQuality, Integer candleCount, String trendLabel,
                            Map<String, String> indicators, List<String> invalidFields) {}
    public record Candidate(String id, String universeAnalysisRunId, String market, String baseAsset,
                            String quoteAsset, Integer rank, String score, String selectionSource,
                            Boolean buyEligible, Boolean sellEligible, String quoteTradeValue24h,
                            Instant createdAt, FeatureSummary features) {
        public static Candidate from(UniverseCandidateRecord c, FeatureSummary features) {
            return new Candidate(AnalysisResponses.id(c.getId()), AnalysisResponses.id(c.getAnalysisRunId()),
                    c.getMarket(), c.getBaseAsset(), c.getQuoteAsset(), c.getRank(), decimal(c.getScore()),
                    c.getSelectionSource(), c.getBuyEligible(), c.getSellEligible(),
                    decimal(c.getQuoteTradeValue24h()), c.getCreatedAt(), features);
        }
    }
    public record Candidates(Scope scope, Universe universe, List<Candidate> items, int page, int size,
                             long totalElements, long totalPages, Instant generatedAt) {
        public Candidates { items = List.copyOf(items); }
    }
    public record Approval(String id, String status, Instant expiresAt, Instant approvedAt,
                           Instant rejectedAt, Instant createdAt, Instant updatedAt) {
        public static Approval from(ApprovalRecord a) {
            return new Approval(AnalysisResponses.id(a.getId()), a.getStatus(), a.getExpiresAt(),
                    a.getApprovedAt(), a.getRejectedAt(), a.getCreatedAt(), a.getUpdatedAt());
        }
    }
    public record Attempt(String id, String approvalRequestId, String orderLogId, String tradingMode,
                          Integer attemptNumber, String status, String errorCode, Instant attemptedAt,
                          Instant nextRetryAt) {
        public static Attempt from(ExecutionAttemptRecord a) {
            return new Attempt(AnalysisResponses.id(a.getId()), AnalysisResponses.id(a.getApprovalRequestId()),
                    AnalysisResponses.id(a.getOrderLogId()), a.getTradingMode(), a.getAttemptNumber(),
                    a.getStatus(), a.getErrorCode(), a.getAttemptedAt(), a.getNextRetryAt());
        }
    }
    public record Order(String id, String approvalRequestId, String tradingMode, String market,
                        String side, String orderType, String status, String amountKrw, String quantity,
                        String price, String executedQuantity, String executedFundsKrw,
                        String averageExecutionPrice, String paidFee, String remainingQuantity,
                        Integer tradesCount, Instant executionSyncedAt, Instant createdAt, Instant updatedAt) {
        public static Order from(OrderRecord o) {
            return new Order(AnalysisResponses.id(o.getId()), AnalysisResponses.id(o.getApprovalRequestId()),
                    o.getTradingMode(), o.getMarket(), o.getSide(), o.getOrderType(), o.getStatus(),
                    decimal(o.getAmountKrw()), decimal(o.getQuantity()), decimal(o.getPrice()),
                    decimal(o.getExecutedQuantity()), decimal(o.getExecutedFundsKrw()),
                    decimal(o.getAverageExecutionPrice()), decimal(o.getPaidFee()), decimal(o.getRemainingQuantity()),
                    o.getTradesCount(), o.getExecutionSyncedAt(), o.getCreatedAt(), o.getUpdatedAt());
        }
    }
    public record Detail(Scope scope, Recommendation recommendation, Analysis analysis,
                         String finalDecisionReason, String recommendedAmountKrw, String recommendedQuantity,
                         Universe universe, Candidate selectedCandidate, List<Approval> approvals,
                         List<Attempt> executionAttempts, Order order, Instant generatedAt) {
        public Detail { approvals = List.copyOf(approvals); executionAttempts = List.copyOf(executionAttempts); }
        public static Detail from(Scope scope, RecommendationRecord r, AnalysisRecord a, Universe universe,
                                  Candidate candidate, List<Approval> approvals, List<Attempt> attempts,
                                  Order order, Instant now) {
            return new Detail(scope, Recommendation.from(r), Analysis.from(a), r.getReason(),
                    decimal(r.getRecommendedAmountKrw()), decimal(r.getRecommendedQuantity()),
                    universe, candidate, approvals, attempts, order, now);
        }
    }
}
