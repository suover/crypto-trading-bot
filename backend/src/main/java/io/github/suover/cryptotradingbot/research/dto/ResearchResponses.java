package io.github.suover.cryptotradingbot.research.dto;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;

// Explicit read DTOs. No raw audit JSON or stored decision signatures.
public final class ResearchResponses {
    private ResearchResponses() {}
    public record Scope(long userId, String exchange, String quoteAsset) {}
    public record Summary(Scope scope, long candidateCount, long shadowEnrollmentCount,
                          long snapshotCount, Long latestSnapshotId,
                          Instant latestSnapshotCapturedAt, Instant generatedAt) {}
    public record Page<T>(List<T> items, int page, int size, long totalElements, long totalPages,
                          Instant generatedAt) {
        public Page { items = List.copyOf(items); }
    }
    public record CandidateItem(Long id, String policyName, Instant registeredAt,
                                String exchange, String quoteAsset, Integer effectiveTopN,
                                boolean shadowEnrolled) {}
    public record RegistrationContext(String datasetSchemaVersion, String baselinePolicySignature,
                                      Long snapshotIdWatermark, Instant capturedAtWatermark,
                                      Instant referenceSnapshotCapturedAt) {}
    public record StoredEnrollment(Long id, Instant shadowEnrolledAt,
                                   String storedGateStatus, Instant storedGateEvaluatedAt) {}
    public record CandidateDetail(Long id, String policyName, Instant registeredAt, String exchange,
                                  String quoteAsset, Integer effectiveTopN, Long referenceSnapshotId,
                                  Map<String, BigDecimal> componentWeights,
                                  RegistrationContext registrationContext, boolean shadowEnrolled,
                                  StoredEnrollment enrollment, Instant generatedAt) {
        public CandidateDetail { componentWeights = Map.copyOf(componentWeights); }
    }
    public record SnapshotItem(Long id, Instant capturedAt, Instant createdAt, String policySignature,
                               Integer researchCandidateCount, Integer prefilterCandidateCount,
                               Integer rankedCandidateCount, Integer finalCandidateCount) {}
    public record AutomationItem(Scope scope, String datasetSchemaVersion, String baselinePolicySignature,
                                 Integer effectiveTopN, Long lastObservedSnapshotId, Long reviewedSnapshotId,
                                 String reviewStatus, String lastStatus, Instant lastAttemptAt,
                                 Long lastReferenceSnapshotId, String lastRegistrationStatus,
                                 Long lastRegisteredSnapshotId, Instant lastRegisteredAt) {}
    public record AutomationStatus(Scope scope, boolean observed, List<AutomationItem> states,
                                   Instant generatedAt) {
        public AutomationStatus { states = List.copyOf(states); }
    }
}
