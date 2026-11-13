package io.github.suover.cryptotradingbot.overview.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;
import java.math.BigDecimal;
import java.time.Instant;

// Minimal read mapping: no relationships, audit JSON, setters or write repository.
@Entity(name = "OverviewTradeRecommendation")
@Table(name = "trade_recommendations")
@Immutable
public class TradeRecommendation {
    @Id
    @Column(name = "id")
    private Long id;
    @Column(name = "user_id")
    private Long userId;
    @Column(name = "exchange")
    private String exchange;
    @Column(name = "analysis_run_id")
    private Long analysisRunId;
    @Column(name = "market")
    private String market;
    @Column(name = "action")
    private String action;
    @Column(name = "confidence", precision = 5, scale = 4)
    private BigDecimal confidence;
    @Column(name = "trade_ratio", precision = 10, scale = 9)
    private BigDecimal tradeRatio;
    @Column(name = "ai_model")
    private String aiModel;
    @Column(name = "status")
    private String status;
    @Column(name = "created_at")
    private Instant createdAt;

    protected TradeRecommendation() {}

    public Long getId() { return id; }
    public Long getUserId() { return userId; }
    public String getExchange() { return exchange; }
    public Long getAnalysisRunId() { return analysisRunId; }
    public String getMarket() { return market; }
    public String getAction() { return action; }
    public BigDecimal getConfidence() { return confidence; }
    public BigDecimal getTradeRatio() { return tradeRatio; }
    public String getAiModel() { return aiModel; }
    public String getStatus() { return status; }
    public Instant getCreatedAt() { return createdAt; }
}

