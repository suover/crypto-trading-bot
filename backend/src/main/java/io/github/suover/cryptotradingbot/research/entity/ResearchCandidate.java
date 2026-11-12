package io.github.suover.cryptotradingbot.research.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;
import java.time.Instant;
import java.util.Map;
import java.math.BigDecimal;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

@Entity
@Table(name = "research_policy_candidates")
@Immutable
public class ResearchCandidate {
    @Id
    @Column(name = "id")
    private Long id;
    @Column(name = "user_id")
    private Long userId;
    @Column(name = "exchange")
    private String exchange;
    @Column(name = "quote_asset")
    private String quoteAsset;
    @Column(name = "scenario_name")
    private String scenarioName;
    @Column(name = "registered_at")
    private Instant registeredAt;
    @Column(name = "effective_top_n")
    private Integer effectiveTopN;
    @Column(name = "reference_snapshot_id")
    private Long referenceSnapshotId;
    @Column(name = "reference_snapshot_captured_at")
    private Instant referenceSnapshotCapturedAt;
    @Column(name = "dataset_schema_version")
    private String datasetSchemaVersion;
    @Column(name = "baseline_policy_signature")
    private String baselinePolicySignature;
    @Column(name = "registration_snapshot_id_watermark")
    private Long registrationSnapshotIdWatermark;
    @Column(name = "registration_captured_at_watermark")
    private Instant registrationCapturedAtWatermark;
    @Column(name = "component_weights", columnDefinition = "jsonb")
    @JdbcTypeCode(SqlTypes.JSON)
    private Map<String, BigDecimal> componentWeights;

    protected ResearchCandidate() {}

    public Long getId() { return id; }
    public Long getUserId() { return userId; }
    public String getExchange() { return exchange; }
    public String getQuoteAsset() { return quoteAsset; }
    public String getScenarioName() { return scenarioName; }
    public Instant getRegisteredAt() { return registeredAt; }
    public Integer getEffectiveTopN() { return effectiveTopN; }
    public Long getReferenceSnapshotId() { return referenceSnapshotId; }
    public Instant getReferenceSnapshotCapturedAt() { return referenceSnapshotCapturedAt; }
    public String getDatasetSchemaVersion() { return datasetSchemaVersion; }
    public String getBaselinePolicySignature() { return baselinePolicySignature; }
    public Long getRegistrationSnapshotIdWatermark() { return registrationSnapshotIdWatermark; }
    public Instant getRegistrationCapturedAtWatermark() { return registrationCapturedAtWatermark; }
    public Map<String, BigDecimal> getComponentWeights() { return componentWeights; }
}
