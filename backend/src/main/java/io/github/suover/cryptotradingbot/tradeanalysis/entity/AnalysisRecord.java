package io.github.suover.cryptotradingbot.tradeanalysis.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;
import java.time.Instant;
import java.math.BigDecimal;

// Read-only mapping; credential identifiers and raw exchange/AI audit payloads are excluded.
@Entity
@Table(name = "analysis_runs")
@Immutable
public class AnalysisRecord {
    @Id
    @Column(name = "id")
    private Long id;
    @Column(name = "user_id")
    private Long userId;
    @Column(name = "pipeline_run_id")
    private String pipelineRunId;
    @Column(name = "run_type")
    private String runType;
    @Column(name = "trading_mode")
    private String tradingMode;
    @Column(name = "status")
    private String status;
    @Column(name = "started_at")
    private Instant startedAt;
    @Column(name = "finished_at")
    private Instant finishedAt;

    protected AnalysisRecord() {}

    public Long getId() { return id; }
    public Long getUserId() { return userId; }
    public String getPipelineRunId() { return pipelineRunId; }
    public String getRunType() { return runType; }
    public String getTradingMode() { return tradingMode; }
    public String getStatus() { return status; }
    public Instant getStartedAt() { return startedAt; }
    public Instant getFinishedAt() { return finishedAt; }
}
