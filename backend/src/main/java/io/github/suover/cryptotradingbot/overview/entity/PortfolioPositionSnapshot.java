package io.github.suover.cryptotradingbot.overview.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;
import java.math.BigDecimal;
import java.time.Instant;

// Minimal read mapping: no relationships, audit JSON, setters or write repository.
@Entity(name = "OverviewPortfolioPositionSnapshot")
@Table(name = "portfolio_position_snapshots")
@Immutable
public class PortfolioPositionSnapshot {
    @Id
    @Column(name = "id")
    private Long id;
    @Column(name = "portfolio_snapshot_id")
    private Long portfolioSnapshotId;
    @Column(name = "exchange")
    private String exchange;
    @Column(name = "currency")
    private String currency;
    @Column(name = "market")
    private String market;
    @Column(name = "available_quantity", precision = 30, scale = 10)
    private BigDecimal availableQuantity;
    @Column(name = "locked_quantity", precision = 30, scale = 10)
    private BigDecimal lockedQuantity;
    @Column(name = "total_quantity", precision = 30, scale = 10)
    private BigDecimal totalQuantity;
    @Column(name = "avg_buy_price", precision = 30, scale = 10)
    private BigDecimal avgBuyPrice;
    @Column(name = "mark_price", precision = 30, scale = 10)
    private BigDecimal markPrice;
    @Column(name = "market_value_krw", precision = 30, scale = 10)
    private BigDecimal marketValueKrw;
    @Column(name = "estimated_cost_basis_krw", precision = 30, scale = 10)
    private BigDecimal estimatedCostBasisKrw;
    @Column(name = "unrealized_pnl_krw", precision = 30, scale = 10)
    private BigDecimal unrealizedPnlKrw;
    @Column(name = "unrealized_pnl_percentage", precision = 30, scale = 10)
    private BigDecimal unrealizedPnlPercentage;
    @Column(name = "valuation_status")
    private String valuationStatus;
    @Column(name = "price_source")
    private String priceSource;

    protected PortfolioPositionSnapshot() {}

    public Long getId() { return id; }
    public Long getPortfolioSnapshotId() { return portfolioSnapshotId; }
    public String getExchange() { return exchange; }
    public String getCurrency() { return currency; }
    public String getMarket() { return market; }
    public BigDecimal getAvailableQuantity() { return availableQuantity; }
    public BigDecimal getLockedQuantity() { return lockedQuantity; }
    public BigDecimal getTotalQuantity() { return totalQuantity; }
    public BigDecimal getAvgBuyPrice() { return avgBuyPrice; }
    public BigDecimal getMarkPrice() { return markPrice; }
    public BigDecimal getMarketValueKrw() { return marketValueKrw; }
    public BigDecimal getEstimatedCostBasisKrw() { return estimatedCostBasisKrw; }
    public BigDecimal getUnrealizedPnlKrw() { return unrealizedPnlKrw; }
    public BigDecimal getUnrealizedPnlPercentage() { return unrealizedPnlPercentage; }
    public String getValuationStatus() { return valuationStatus; }
    public String getPriceSource() { return priceSource; }
}

