package io.github.suover.cryptotradingbot.research.service;

import io.github.suover.cryptotradingbot.global.security.CurrentTradingScope;
import io.github.suover.cryptotradingbot.research.dto.CandidateSearch;
import io.github.suover.cryptotradingbot.research.dto.ResearchResponses.*;
import io.github.suover.cryptotradingbot.research.repository.*;
import java.time.Clock;
import java.time.Instant;
import java.util.List;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
public class ResearchReadService {
    private final ResearchReadRepository repository;
    private final CurrentTradingScope tradingScope;
    private final Clock clock;

    public ResearchReadService(ResearchReadRepository repository, CurrentTradingScope tradingScope, Clock clock) {
        this.repository = repository;
        this.tradingScope = tradingScope;
        this.clock = clock;
    }
    public Summary summary() {
        var scope = tradingScope.resolve();
        var latest = repository.latestSnapshot(scope).orElse(null);
        return new Summary(responseScope(scope), repository.candidateCount(scope), repository.enrollmentCount(scope),
                repository.snapshotCount(scope), latest == null ? null : latest.id(),
                latest == null ? null : latest.capturedAt(), clock.instant());
    }
    public Page<CandidateItem> candidates(CandidateSearch search) {
        var scope = tradingScope.resolve();
        return page(repository.candidates(scope, search), search.page(), search.size(),
                repository.candidateCount(scope, search));
    }
    public CandidateDetail candidate(long id) {
        if (id <= 0) throw new IllegalArgumentException("Candidate id must be positive");
        var scope = tradingScope.resolve();
        var c = repository.candidate(scope, id).orElseThrow(ResearchNotFoundException::new);
        var enrollment = repository.enrollment(scope, id).orElse(null);
        return new CandidateDetail(c.getId(), c.getScenarioName(), c.getRegisteredAt(),
                c.getExchange(), c.getQuoteAsset(), c.getEffectiveTopN(), c.getReferenceSnapshotId(),
                c.getComponentWeights(), new RegistrationContext(c.getDatasetSchemaVersion(),
                c.getBaselinePolicySignature(), c.getRegistrationSnapshotIdWatermark(),
                c.getRegistrationCapturedAtWatermark(), c.getReferenceSnapshotCapturedAt()),
                enrollment != null, enrollment, clock.instant());
    }
    public Page<SnapshotItem> snapshots(int page, int size) {
        CandidateSearch.validatePage(page, size);
        var scope = tradingScope.resolve();
        return page(repository.snapshots(scope, page, size), page, size, repository.snapshotCount(scope));
    }
    public AutomationStatus automationStatus() {
        var scope = tradingScope.resolve();
        var states = repository.automationStates(scope).stream().map(a ->
                new AutomationItem(responseScope(scope), a.getId().getDatasetSchemaVersion(),
                        a.getId().getBaselinePolicySignature(), a.getId().getEffectiveTopN(),
                        a.getLastObservedSnapshotId(), a.getReviewedSnapshotId(), a.getReviewStatus(),
                        a.getLastStatus(), a.getLastAttemptAt(), a.getLastReferenceSnapshotId(),
                        a.getLastRegistrationStatus(), a.getLastRegisteredSnapshotId(),
                        a.getLastRegisteredAt())).toList();
        return new AutomationStatus(responseScope(scope), !states.isEmpty(), states, clock.instant());
    }
    private <T> Page<T> page(List<T> items, int page, int size, long total) {
        return new Page<>(items, page, size, total, total / size + (total % size == 0 ? 0 : 1),
                Instant.now(clock));
    }
    private static Scope responseScope(CurrentTradingScope.Scope scope) {
        return new Scope(scope.tradingUserId(), scope.exchange(), scope.quoteAsset());
    }
}
