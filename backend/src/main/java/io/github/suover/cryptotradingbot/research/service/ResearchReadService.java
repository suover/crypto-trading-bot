package io.github.suover.cryptotradingbot.research.service;

import io.github.suover.cryptotradingbot.global.config.BackendProperties;
import io.github.suover.cryptotradingbot.research.dto.CandidateSearch;
import io.github.suover.cryptotradingbot.research.dto.ResearchResponses.*;
import io.github.suover.cryptotradingbot.research.entity.TradingUser;
import io.github.suover.cryptotradingbot.research.repository.*;
import java.time.Clock;
import java.time.Instant;
import java.util.List;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
public class ResearchReadService {
    private final ResearchReadRepository repository;
    private final TradingUserRepository users;
    private final BackendProperties properties;
    private final Clock clock;

    public ResearchReadService(ResearchReadRepository repository, TradingUserRepository users,
                               BackendProperties properties, Clock clock) {
        this.repository = repository;
        this.users = users;
        this.properties = properties;
        this.clock = clock;
    }
    private Scope validateScope() {
        if (!users.findById(properties.tradingUserId()).map(TradingUser::getIsActive).orElse(false)) {
            throw new AccessDeniedException("Configured trading user is unavailable");
        }
        return new Scope(properties.tradingUserId(), properties.exchange(), properties.quoteAsset());
    }
    public Summary summary() {
        Scope scope = validateScope();
        var latest = repository.latestSnapshot().orElse(null);
        return new Summary(scope, repository.candidateCount(), repository.enrollmentCount(),
                repository.snapshotCount(), latest == null ? null : latest.id(),
                latest == null ? null : latest.capturedAt(), clock.instant());
    }
    public Page<CandidateItem> candidates(CandidateSearch search) {
        validateScope();
        return page(repository.candidates(search), search.page(), search.size(),
                repository.candidateCount(search));
    }
    public CandidateDetail candidate(long id) {
        if (id <= 0) throw new IllegalArgumentException("Candidate id must be positive");
        validateScope();
        var c = repository.candidate(id).orElseThrow(ResearchNotFoundException::new);
        var enrollment = repository.enrollment(id).orElse(null);
        return new CandidateDetail(c.getId(), c.getScenarioName(), c.getRegisteredAt(),
                c.getExchange(), c.getQuoteAsset(), c.getEffectiveTopN(), c.getReferenceSnapshotId(),
                c.getComponentWeights(), new RegistrationContext(c.getDatasetSchemaVersion(),
                c.getBaselinePolicySignature(), c.getRegistrationSnapshotIdWatermark(),
                c.getRegistrationCapturedAtWatermark(), c.getReferenceSnapshotCapturedAt()),
                enrollment != null, enrollment, clock.instant());
    }
    public Page<SnapshotItem> snapshots(int page, int size) {
        CandidateSearch.validatePage(page, size);
        validateScope();
        return page(repository.snapshots(page, size), page, size, repository.snapshotCount());
    }
    public AutomationStatus automationStatus() {
        Scope scope = validateScope();
        var states = repository.automationStates().stream().map(a ->
                new AutomationItem(scope, a.getId().getDatasetSchemaVersion(),
                        a.getId().getBaselinePolicySignature(), a.getId().getEffectiveTopN(),
                        a.getLastObservedSnapshotId(), a.getReviewedSnapshotId(), a.getReviewStatus(),
                        a.getLastStatus(), a.getLastAttemptAt(), a.getLastReferenceSnapshotId(),
                        a.getLastRegistrationStatus(), a.getLastRegisteredSnapshotId(),
                        a.getLastRegisteredAt())).toList();
        return new AutomationStatus(scope, !states.isEmpty(), states, clock.instant());
    }
    private <T> Page<T> page(List<T> items, int page, int size, long total) {
        return new Page<>(items, page, size, total, total / size + (total % size == 0 ? 0 : 1),
                Instant.now(clock));
    }
}
