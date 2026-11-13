package io.github.suover.cryptotradingbot.overview.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;
import java.math.BigDecimal;
import java.time.Instant;

// Minimal read mapping: no relationships, audit JSON, setters or write repository.
@Entity(name = "OverviewBotTradingPnlSummary")
@Table(name = "bot_trading_pnl_summaries")
@Immutable
public class BotTradingPnlSummary {
    @Id
    @Column(name = "id")
    private Long id;
    @Column(name = "user_id")
    private Long userId;
    @Column(name = "exchange")
    private String exchange;
    @Column(name = "accounting_status")
    private String accountingStatus;
    @Column(name = "recognized_realized_pnl_krw", precision = 30, scale = 10)
    private BigDecimal recognizedRealizedPnlKrw;
    @Column(name = "recognized_realized_return_percentage", precision = 30, scale = 10)
    private BigDecimal recognizedRealizedReturnPercentage;
    @Column(name = "total_fees_krw", precision = 30, scale = 10)
    private BigDecimal totalFeesKrw;
    @Column(name = "processed_order_count")
    private Integer processedOrderCount;
    @Column(name = "fully_matched_sell_count")
    private Integer fullyMatchedSellCount;
    @Column(name = "partially_matched_sell_count")
    private Integer partiallyMatchedSellCount;
    @Column(name = "unmatched_sell_count")
    private Integer unmatchedSellCount;
    @Column(name = "incomplete_order_count")
    private Integer incompleteOrderCount;
    @Column(name = "calculated_at")
    private Instant calculatedAt;
    @Column(name = "source_last_updated_at")
    private Instant sourceLastUpdatedAt;

    protected BotTradingPnlSummary() {}

    public Long getId() { return id; }
    public Long getUserId() { return userId; }
    public String getExchange() { return exchange; }
    public String getAccountingStatus() { return accountingStatus; }
    public BigDecimal getRecognizedRealizedPnlKrw() { return recognizedRealizedPnlKrw; }
    public BigDecimal getRecognizedRealizedReturnPercentage() { return recognizedRealizedReturnPercentage; }
    public BigDecimal getTotalFeesKrw() { return totalFeesKrw; }
    public Integer getProcessedOrderCount() { return processedOrderCount; }
    public Integer getFullyMatchedSellCount() { return fullyMatchedSellCount; }
    public Integer getPartiallyMatchedSellCount() { return partiallyMatchedSellCount; }
    public Integer getUnmatchedSellCount() { return unmatchedSellCount; }
    public Integer getIncompleteOrderCount() { return incompleteOrderCount; }
    public Instant getCalculatedAt() { return calculatedAt; }
    public Instant getSourceLastUpdatedAt() { return sourceLastUpdatedAt; }
}

