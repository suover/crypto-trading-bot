package io.github.suover.cryptotradingbot.tradeanalysis.service;

import io.github.suover.cryptotradingbot.global.security.CurrentTradingScope;
import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisQuery;
import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisResponses.*;
import io.github.suover.cryptotradingbot.tradeanalysis.entity.*;
import io.github.suover.cryptotradingbot.tradeanalysis.repository.AnalysisReadRepository;
import java.time.Clock;
import java.util.List;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
public class AnalysisReadService {
    private final AnalysisReadRepository repository;
    private final CurrentTradingScope tradingScope;
    private final StoredFeatureProjection features;
    private final Clock clock;
    public AnalysisReadService(AnalysisReadRepository repository, CurrentTradingScope tradingScope, StoredFeatureProjection features, Clock clock) {
        this.repository = repository; this.tradingScope = tradingScope;
        this.features = features; this.clock = clock;
    }
    public Page<Recommendation> recommendations(AnalysisQuery.Search filter) {
        var scope = tradingScope.resolve();
        var items = repository.recommendations(scope, filter).stream().map(Recommendation::from).toList();
        long total = repository.count(scope, filter);
        return new Page<>(responseScope(scope), items, filter.page(), filter.size(), total, pages(total, filter.size()), clock.instant());
    }
    public Detail detail(long id) {
        var scope = tradingScope.resolve();
        var r = recommendation(scope, id);
        var a = repository.analysis(scope, r.getAnalysisRunId());
        if (a == null) throw new AnalysisNotFoundException();
        var c = repository.selectedCandidate(scope, r, a).orElse(null);
        return Detail.from(responseScope(scope), r, a, universe(r, c), candidate(c),
                repository.approvals(scope, id).stream().map(Approval::from).toList(),
                repository.attempts(scope, id).stream().map(Attempt::from).toList(),
                repository.order(scope, id).map(Order::from).orElse(null), clock.instant());
    }
    public Candidates candidates(long id, AnalysisQuery.Pagination filter) {
        var scope = tradingScope.resolve();
        var r = recommendation(scope, id);
        var a = repository.analysis(scope, r.getAnalysisRunId());
        if (a == null) throw new AnalysisNotFoundException();
        var selected = repository.selectedCandidate(scope, r, a).orElse(null);
        var universe = universe(r, selected);
        if (selected == null)
            return new Candidates(responseScope(scope), universe, List.of(), filter.page(), filter.size(), 0, 0, clock.instant());
        var rows = repository.candidates(scope, selected.getAnalysisRunId(), filter).stream().map(this::candidate).toList();
        long total = repository.candidateCount(scope, selected.getAnalysisRunId());
        return new Candidates(responseScope(scope), universe, rows, filter.page(), filter.size(), total,
                pages(total, filter.size()), clock.instant());
    }
    private RecommendationRecord recommendation(CurrentTradingScope.Scope scope, long id) {
        if (id <= 0) throw new IllegalArgumentException("Invalid recommendation ID");
        return repository.recommendation(scope, id).orElseThrow(AnalysisNotFoundException::new);
    }
    private static Universe universe(RecommendationRecord r, UniverseCandidateRecord c) {
        if (r.getUniverseCandidateId() == null) return new Universe("NO_REFERENCE", null, null);
        return c == null ? new Universe("INVALID_REFERENCE", null, null)
                : new Universe("VERIFIED", c.getAnalysisRunId().toString(), c.getId().toString());
    }
    private Candidate candidate(UniverseCandidateRecord c) {
        return c == null ? null : Candidate.from(c, features.read(c.getFeatureData()));
    }
    private static long pages(long total, int size) { return total / size + (total % size == 0 ? 0 : 1); }
    private static Scope responseScope(CurrentTradingScope.Scope scope) {
        return new Scope(Long.toString(scope.tradingUserId()), scope.exchange(), scope.quoteAsset());
    }
}
