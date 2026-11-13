package io.github.suover.cryptotradingbot.tradeanalysis.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;
import java.time.Instant;
import java.math.BigDecimal;

// Read-only mapping; credential identifiers and raw exchange/AI audit payloads are excluded.
@Entity
@Table(name = "order_execution_attempts")
@Immutable
public class ExecutionAttemptRecord {
    @Id
    @Column(name = "id")
    private Long id;
    @Column(name = "recommendation_id")
    private Long recommendationId;
    @Column(name = "approval_request_id")
    private Long approvalRequestId;
    @Column(name = "order_log_id")
    private Long orderLogId;
    @Column(name = "user_id")
    private Long userId;
    @Column(name = "trading_mode")
    private String tradingMode;
    @Column(name = "attempt_number")
    private Integer attemptNumber;
    @Column(name = "status")
    private String status;
    @Column(name = "error_code")
    private String errorCode;
    @Column(name = "attempted_at")
    private Instant attemptedAt;
    @Column(name = "next_retry_at")
    private Instant nextRetryAt;

    protected ExecutionAttemptRecord() {}

    public Long getId() { return id; }
    public Long getRecommendationId() { return recommendationId; }
    public Long getApprovalRequestId() { return approvalRequestId; }
    public Long getOrderLogId() { return orderLogId; }
    public Long getUserId() { return userId; }
    public String getTradingMode() { return tradingMode; }
    public Integer getAttemptNumber() { return attemptNumber; }
    public String getStatus() { return status; }
    public String getErrorCode() { return errorCode; }
    public Instant getAttemptedAt() { return attemptedAt; }
    public Instant getNextRetryAt() { return nextRetryAt; }
}
