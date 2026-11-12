package io.github.suover.cryptotradingbot.research.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;
import java.time.Instant;

@Entity
@Table(name = "strategy_replay_snapshots")
@Immutable
public class ReplaySnapshot {
    @Id
    @Column(name = "id")
    private Long id;
    @Column(name = "user_id")
    private Long userId;
    @Column(name = "exchange")
    private String exchange;
    @Column(name = "quote_asset")
    private String quoteAsset;
    @Column(name = "policy_signature")
    private String policySignature;
    @Column(name = "research_candidate_count")
    private Integer researchCandidateCount;
    @Column(name = "prefilter_candidate_count")
    private Integer prefilterCandidateCount;
    @Column(name = "ranked_candidate_count")
    private Integer rankedCandidateCount;
    @Column(name = "final_candidate_count")
    private Integer finalCandidateCount;
    @Column(name = "captured_at")
    private Instant capturedAt;
    @Column(name = "created_at")
    private Instant createdAt;

    protected ReplaySnapshot() {}

    public Long getId() { return id; }
    public Long getUserId() { return userId; }
    public String getExchange() { return exchange; }
    public String getQuoteAsset() { return quoteAsset; }
    public String getPolicySignature() { return policySignature; }
    public Integer getResearchCandidateCount() { return researchCandidateCount; }
    public Integer getPrefilterCandidateCount() { return prefilterCandidateCount; }
    public Integer getRankedCandidateCount() { return rankedCandidateCount; }
    public Integer getFinalCandidateCount() { return finalCandidateCount; }
    public Instant getCapturedAt() { return capturedAt; }
    public Instant getCreatedAt() { return createdAt; }
}
