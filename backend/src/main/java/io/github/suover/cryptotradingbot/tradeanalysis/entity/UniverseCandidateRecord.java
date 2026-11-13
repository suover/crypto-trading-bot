package io.github.suover.cryptotradingbot.tradeanalysis.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;
import java.time.Instant;
import java.math.BigDecimal;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

// Read-only mapping; credential identifiers and raw exchange/AI audit payloads are excluded.
@Entity
@Table(name = "market_universe_candidates")
@Immutable
public class UniverseCandidateRecord {
    @Id
    @Column(name = "id")
    private Long id;
    @Column(name = "analysis_run_id")
    private Long analysisRunId;
    @Column(name = "user_id")
    private Long userId;
    @Column(name = "exchange")
    private String exchange;
    @Column(name = "market")
    private String market;
    @Column(name = "base_asset")
    private String baseAsset;
    @Column(name = "quote_asset")
    private String quoteAsset;
    @Column(name = "rank")
    private Integer rank;
    @Column(name = "score", precision = 18, scale = 9)
    private BigDecimal score;
    @Column(name = "selection_source")
    private String selectionSource;
    @Column(name = "buy_eligible")
    private Boolean buyEligible;
    @Column(name = "sell_eligible")
    private Boolean sellEligible;
    @Column(name = "quote_trade_value_24h", precision = 30, scale = 2)
    private BigDecimal quoteTradeValue24h;
    @Column(name = "feature_data", columnDefinition = "jsonb")
    @JdbcTypeCode(SqlTypes.JSON)
    private String featureData;
    @Column(name = "created_at")
    private Instant createdAt;

    protected UniverseCandidateRecord() {}

    public Long getId() { return id; }
    public Long getAnalysisRunId() { return analysisRunId; }
    public Long getUserId() { return userId; }
    public String getExchange() { return exchange; }
    public String getMarket() { return market; }
    public String getBaseAsset() { return baseAsset; }
    public String getQuoteAsset() { return quoteAsset; }
    public Integer getRank() { return rank; }
    public BigDecimal getScore() { return score; }
    public String getSelectionSource() { return selectionSource; }
    public Boolean getBuyEligible() { return buyEligible; }
    public Boolean getSellEligible() { return sellEligible; }
    public BigDecimal getQuoteTradeValue24h() { return quoteTradeValue24h; }
    public String getFeatureData() { return featureData; }
    public Instant getCreatedAt() { return createdAt; }
}
