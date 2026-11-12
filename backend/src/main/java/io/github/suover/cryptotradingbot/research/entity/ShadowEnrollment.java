package io.github.suover.cryptotradingbot.research.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;
import java.time.Instant;

@Entity
@Table(name = "shadow_policy_enrollments")
@Immutable
public class ShadowEnrollment {
    @Id
    @Column(name = "id")
    private Long id;
    @Column(name = "candidate_id")
    private Long candidateId;
    @Column(name = "user_id")
    private Long userId;
    @Column(name = "exchange")
    private String exchange;
    @Column(name = "quote_asset")
    private String quoteAsset;
    @Column(name = "baseline_policy_signature")
    private String baselinePolicySignature;
    @Column(name = "dataset_schema_version")
    private String datasetSchemaVersion;
    @Column(name = "effective_top_n")
    private Integer effectiveTopN;
    @Column(name = "shadow_enrolled_at")
    private Instant shadowEnrolledAt;
    @Column(name = "gate_status")
    private String gateStatus;
    @Column(name = "gate_evaluated_at")
    private Instant gateEvaluatedAt;

    protected ShadowEnrollment() {}

    public Long getId() { return id; }
    public Long getCandidateId() { return candidateId; }
    public Long getUserId() { return userId; }
    public String getExchange() { return exchange; }
    public String getQuoteAsset() { return quoteAsset; }
    public String getBaselinePolicySignature() { return baselinePolicySignature; }
    public String getDatasetSchemaVersion() { return datasetSchemaVersion; }
    public Integer getEffectiveTopN() { return effectiveTopN; }
    public Instant getShadowEnrolledAt() { return shadowEnrolledAt; }
    public String getGateStatus() { return gateStatus; }
    public Instant getGateEvaluatedAt() { return gateEvaluatedAt; }
}
