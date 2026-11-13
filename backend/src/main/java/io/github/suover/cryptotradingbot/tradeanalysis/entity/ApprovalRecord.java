package io.github.suover.cryptotradingbot.tradeanalysis.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;
import java.time.Instant;
import java.math.BigDecimal;

// Read-only mapping; credential identifiers and raw exchange/AI audit payloads are excluded.
@Entity
@Table(name = "approval_requests")
@Immutable
public class ApprovalRecord {
    @Id
    @Column(name = "id")
    private Long id;
    @Column(name = "recommendation_id")
    private Long recommendationId;
    @Column(name = "user_id")
    private Long userId;
    @Column(name = "status")
    private String status;
    @Column(name = "expires_at")
    private Instant expiresAt;
    @Column(name = "approved_at")
    private Instant approvedAt;
    @Column(name = "rejected_at")
    private Instant rejectedAt;
    @Column(name = "created_at")
    private Instant createdAt;
    @Column(name = "updated_at")
    private Instant updatedAt;

    protected ApprovalRecord() {}

    public Long getId() { return id; }
    public Long getRecommendationId() { return recommendationId; }
    public Long getUserId() { return userId; }
    public String getStatus() { return status; }
    public Instant getExpiresAt() { return expiresAt; }
    public Instant getApprovedAt() { return approvedAt; }
    public Instant getRejectedAt() { return rejectedAt; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
