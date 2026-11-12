package io.github.suover.cryptotradingbot.research.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;
import java.time.Instant;

@Entity
@Table(name = "research_candidate_automation_states")
@Immutable
public class AutomationState {
    @EmbeddedId
    private AutomationStateId id;
    @Column(name = "last_observed_snapshot_id")
    private Long lastObservedSnapshotId;
    @Column(name = "reviewed_snapshot_id")
    private Long reviewedSnapshotId;
    @Column(name = "review_status")
    private String reviewStatus;
    @Column(name = "last_status")
    private String lastStatus;
    @Column(name = "last_attempt_at")
    private Instant lastAttemptAt;
    @Column(name = "last_reference_snapshot_id")
    private Long lastReferenceSnapshotId;
    @Column(name = "last_registration_status")
    private String lastRegistrationStatus;
    @Column(name = "last_registered_snapshot_id")
    private Long lastRegisteredSnapshotId;
    @Column(name = "last_registered_at")
    private Instant lastRegisteredAt;

    protected AutomationState() {}

    public AutomationStateId getId() { return id; }
    public Long getLastObservedSnapshotId() { return lastObservedSnapshotId; }
    public Long getReviewedSnapshotId() { return reviewedSnapshotId; }
    public String getReviewStatus() { return reviewStatus; }
    public String getLastStatus() { return lastStatus; }
    public Instant getLastAttemptAt() { return lastAttemptAt; }
    public Long getLastReferenceSnapshotId() { return lastReferenceSnapshotId; }
    public String getLastRegistrationStatus() { return lastRegistrationStatus; }
    public Long getLastRegisteredSnapshotId() { return lastRegisteredSnapshotId; }
    public Instant getLastRegisteredAt() { return lastRegisteredAt; }
}
