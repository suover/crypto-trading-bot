package io.github.suover.cryptotradingbot.overview.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;
import java.math.BigDecimal;
import java.time.Instant;

// Minimal read mapping: no relationships, audit JSON, setters or write repository.
@Entity(name = "OverviewPortfolioSnapshot")
@Table(name = "portfolio_snapshots")
@Immutable
public class PortfolioSnapshot {
    @Id
    @Column(name = "id")
    private Long id;
    @Column(name = "user_id")
    private Long userId;
    @Column(name = "exchange")
    private String exchange;
    @Column(name = "quote_asset")
    private String quoteAsset;
    @Column(name = "captured_at")
    private Instant capturedAt;
    @Column(name = "valuation_status")
    private String valuationStatus;
    @Column(name = "total_value_krw", precision = 30, scale = 10)
    private BigDecimal totalValueKrw;
    @Column(name = "known_total_value_krw", precision = 30, scale = 10)
    private BigDecimal knownTotalValueKrw;
    @Column(name = "priced_positions_value_krw", precision = 30, scale = 10)
    private BigDecimal pricedPositionsValueKrw;
    @Column(name = "cash_total_krw", precision = 30, scale = 10)
    private BigDecimal cashTotalKrw;
    @Column(name = "unrealized_pnl_krw", precision = 30, scale = 10)
    private BigDecimal unrealizedPnlKrw;
    @Column(name = "position_count")
    private Integer positionCount;
    @Column(name = "unpriced_asset_count")
    private Integer unpricedAssetCount;
    @Column(name = "missing_cost_basis_count")
    private Integer missingCostBasisCount;

    protected PortfolioSnapshot() {}

    public Long getId() { return id; }
    public Long getUserId() { return userId; }
    public String getExchange() { return exchange; }
    public String getQuoteAsset() { return quoteAsset; }
    public Instant getCapturedAt() { return capturedAt; }
    public String getValuationStatus() { return valuationStatus; }
    public BigDecimal getTotalValueKrw() { return totalValueKrw; }
    public BigDecimal getKnownTotalValueKrw() { return knownTotalValueKrw; }
    public BigDecimal getPricedPositionsValueKrw() { return pricedPositionsValueKrw; }
    public BigDecimal getCashTotalKrw() { return cashTotalKrw; }
    public BigDecimal getUnrealizedPnlKrw() { return unrealizedPnlKrw; }
    public Integer getPositionCount() { return positionCount; }
    public Integer getUnpricedAssetCount() { return unpricedAssetCount; }
    public Integer getMissingCostBasisCount() { return missingCostBasisCount; }
}

