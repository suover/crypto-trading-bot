package io.github.suover.cryptotradingbot.overview.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;
import java.math.BigDecimal;
import java.time.Instant;

// Minimal read mapping: no relationships, audit JSON, setters or write repository.
@Entity(name = "OverviewPortfolioPerformanceSnapshot")
@Table(name = "portfolio_performance_snapshots")
@Immutable
public class PortfolioPerformanceSnapshot {
    @Id
    @Column(name = "id")
    private Long id;
    @Column(name = "user_id")
    private Long userId;
    @Column(name = "exchange")
    private String exchange;
    @Column(name = "portfolio_snapshot_id")
    private Long portfolioSnapshotId;
    @Column(name = "period_start_at")
    private Instant periodStartAt;
    @Column(name = "period_end_at")
    private Instant periodEndAt;
    @Column(name = "return_method")
    private String returnMethod;
    @Column(name = "period_return_percentage", precision = 30, scale = 10)
    private BigDecimal periodReturnPercentage;
    @Column(name = "cumulative_return_percentage", precision = 30, scale = 10)
    private BigDecimal cumulativeReturnPercentage;
    @Column(name = "performance_index", precision = 30, scale = 10)
    private BigDecimal performanceIndex;
    @Column(name = "drawdown_index", precision = 30, scale = 10)
    private BigDecimal drawdownIndex;
    @Column(name = "drawdown_percentage", precision = 30, scale = 10)
    private BigDecimal drawdownPercentage;
    @Column(name = "max_drawdown_percentage", precision = 30, scale = 10)
    private BigDecimal maxDrawdownPercentage;
    @Column(name = "performance_status")
    private String performanceStatus;
    @Column(name = "safe_reason")
    private String safeReason;
    @Column(name = "calculated_at")
    private Instant calculatedAt;
    @Column(name = "end_value_krw", precision = 30, scale = 10)
    private BigDecimal endValueKrw;

    protected PortfolioPerformanceSnapshot() {}

    public Long getId() { return id; }
    public Long getUserId() { return userId; }
    public String getExchange() { return exchange; }
    public Long getPortfolioSnapshotId() { return portfolioSnapshotId; }
    public Instant getPeriodStartAt() { return periodStartAt; }
    public Instant getPeriodEndAt() { return periodEndAt; }
    public String getReturnMethod() { return returnMethod; }
    public BigDecimal getPeriodReturnPercentage() { return periodReturnPercentage; }
    public BigDecimal getCumulativeReturnPercentage() { return cumulativeReturnPercentage; }
    public BigDecimal getPerformanceIndex() { return performanceIndex; }
    public BigDecimal getDrawdownIndex() { return drawdownIndex; }
    public BigDecimal getDrawdownPercentage() { return drawdownPercentage; }
    public BigDecimal getMaxDrawdownPercentage() { return maxDrawdownPercentage; }
    public String getPerformanceStatus() { return performanceStatus; }
    public String getSafeReason() { return safeReason; }
    public Instant getCalculatedAt() { return calculatedAt; }
    public BigDecimal getEndValueKrw() { return endValueKrw; }
}

