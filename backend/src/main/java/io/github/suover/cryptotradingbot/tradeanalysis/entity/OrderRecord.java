package io.github.suover.cryptotradingbot.tradeanalysis.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;
import java.time.Instant;
import java.math.BigDecimal;

// Read-only mapping; credential identifiers and raw exchange/AI audit payloads are excluded.
@Entity
@Table(name = "order_logs")
@Immutable
public class OrderRecord {
    @Id
    @Column(name = "id")
    private Long id;
    @Column(name = "recommendation_id")
    private Long recommendationId;
    @Column(name = "approval_request_id")
    private Long approvalRequestId;
    @Column(name = "user_id")
    private Long userId;
    @Column(name = "trading_mode")
    private String tradingMode;
    @Column(name = "exchange")
    private String exchange;
    @Column(name = "market")
    private String market;
    @Column(name = "side")
    private String side;
    @Column(name = "order_type")
    private String orderType;
    @Column(name = "amount_krw", precision = 20, scale = 2)
    private BigDecimal amountKrw;
    @Column(name = "quantity", precision = 30, scale = 10)
    private BigDecimal quantity;
    @Column(name = "price", precision = 30, scale = 10)
    private BigDecimal price;
    @Column(name = "status")
    private String status;
    @Column(name = "executed_quantity", precision = 30, scale = 10)
    private BigDecimal executedQuantity;
    @Column(name = "executed_funds_krw", precision = 30, scale = 10)
    private BigDecimal executedFundsKrw;
    @Column(name = "average_execution_price", precision = 30, scale = 10)
    private BigDecimal averageExecutionPrice;
    @Column(name = "paid_fee", precision = 30, scale = 10)
    private BigDecimal paidFee;
    @Column(name = "remaining_quantity", precision = 30, scale = 10)
    private BigDecimal remainingQuantity;
    @Column(name = "trades_count")
    private Integer tradesCount;
    @Column(name = "execution_synced_at")
    private Instant executionSyncedAt;
    @Column(name = "created_at")
    private Instant createdAt;
    @Column(name = "updated_at")
    private Instant updatedAt;

    protected OrderRecord() {}

    public Long getId() { return id; }
    public Long getRecommendationId() { return recommendationId; }
    public Long getApprovalRequestId() { return approvalRequestId; }
    public Long getUserId() { return userId; }
    public String getTradingMode() { return tradingMode; }
    public String getExchange() { return exchange; }
    public String getMarket() { return market; }
    public String getSide() { return side; }
    public String getOrderType() { return orderType; }
    public BigDecimal getAmountKrw() { return amountKrw; }
    public BigDecimal getQuantity() { return quantity; }
    public BigDecimal getPrice() { return price; }
    public String getStatus() { return status; }
    public BigDecimal getExecutedQuantity() { return executedQuantity; }
    public BigDecimal getExecutedFundsKrw() { return executedFundsKrw; }
    public BigDecimal getAverageExecutionPrice() { return averageExecutionPrice; }
    public BigDecimal getPaidFee() { return paidFee; }
    public BigDecimal getRemainingQuantity() { return remainingQuantity; }
    public Integer getTradesCount() { return tradesCount; }
    public Instant getExecutionSyncedAt() { return executionSyncedAt; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
