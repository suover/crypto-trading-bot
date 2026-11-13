package io.github.suover.cryptotradingbot.tradeanalysis.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;
import java.time.Instant;
import java.math.BigDecimal;

// Read-only mapping; credential identifiers and raw exchange/AI audit payloads are excluded.
@Entity
@Table(name = "trade_recommendations")
@Immutable
public class RecommendationRecord {
    @Id
    @Column(name = "id")
    private Long id;
    @Column(name = "user_id")
    private Long userId;
    @Column(name = "analysis_run_id")
    private Long analysisRunId;
    @Column(name = "universe_candidate_id")
    private Long universeCandidateId;
    @Column(name = "exchange")
    private String exchange;
    @Column(name = "market")
    private String market;
    @Column(name = "action")
    private String action;
    @Column(name = "trade_ratio", precision = 10, scale = 9)
    private BigDecimal tradeRatio;
    @Column(name = "confidence", precision = 5, scale = 4)
    private BigDecimal confidence;
    @Column(name = "reason", columnDefinition = "text")
    private String reason;
    @Column(name = "recommended_amount_krw", precision = 20, scale = 2)
    private BigDecimal recommendedAmountKrw;
    @Column(name = "recommended_quantity", precision = 30, scale = 10)
    private BigDecimal recommendedQuantity;
    @Column(name = "ai_model")
    private String aiModel;
    @Column(name = "status")
    private String status;
    @Column(name = "created_at")
    private Instant createdAt;
    @Column(name = "updated_at")
    private Instant updatedAt;

    protected RecommendationRecord() {}

    public Long getId() { return id; }
    public Long getUserId() { return userId; }
    public Long getAnalysisRunId() { return analysisRunId; }
    public Long getUniverseCandidateId() { return universeCandidateId; }
    public String getExchange() { return exchange; }
    public String getMarket() { return market; }
    public String getAction() { return action; }
    public BigDecimal getTradeRatio() { return tradeRatio; }
    public BigDecimal getConfidence() { return confidence; }
    public String getReason() { return reason; }
    public BigDecimal getRecommendedAmountKrw() { return recommendedAmountKrw; }
    public BigDecimal getRecommendedQuantity() { return recommendedQuantity; }
    public String getAiModel() { return aiModel; }
    public String getStatus() { return status; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
