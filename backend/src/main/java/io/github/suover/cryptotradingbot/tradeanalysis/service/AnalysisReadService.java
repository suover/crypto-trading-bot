package io.github.suover.cryptotradingbot.tradeanalysis.service;

import io.github.suover.cryptotradingbot.global.config.BackendProperties;
import io.github.suover.cryptotradingbot.research.entity.TradingUser;
import io.github.suover.cryptotradingbot.research.repository.TradingUserRepository;
import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisQuery;
import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisResponses.*;
import io.github.suover.cryptotradingbot.tradeanalysis.entity.*;
import io.github.suover.cryptotradingbot.tradeanalysis.repository.AnalysisReadRepository;
import java.time.Clock;
import java.util.List;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
public class AnalysisReadService {
    private final AnalysisReadRepository repository;
    private final TradingUserRepository users;
    private final BackendProperties properties;
    private final StoredFeatureProjection features;
    private final Clock clock;
    public AnalysisReadService(AnalysisReadRepository repository, TradingUserRepository users,
                               BackendProperties properties, StoredFeatureProjection features, Clock clock) {
        this.repository = repository; this.users = users; this.properties = properties;
        this.features = features; this.clock = clock;
    }
    private Scope scope() {
        if (!users.findById(properties.tradingUserId()).map(TradingUser::getIsActive).orElse(false))
            throw new AccessDeniedException("Configured trading user is unavailable");
        return new Scope(Long.toString(properties.tradingUserId()), properties.exchange(), properties.quoteAsset());
    }
    public Page<Recommendation> recommendations(AnalysisQuery.Search filter) {
        var scope = scope();
        var items = repository.recommendations(filter).stream().map(Recommendation::from).toList();
        long total = repository.count(filter);
        return new Page<>(scope, items, filter.page(), filter.size(), total, pages(total, filter.size()), clock.instant());
    }
    public Detail detail(long id) {
        var scope = scope();
        var r = recommendation(id);
        var a = repository.analysis(r.getAnalysisRunId());
        if (a == null) throw new AnalysisNotFoundException();
        var c = repository.selectedCandidate(r, a).orElse(null);
        return Detail.from(scope, r, a, universe(r, c), candidate(c),
                repository.approvals(id).stream().map(Approval::from).toList(),
                repository.attempts(id).stream().map(Attempt::from).toList(),
                repository.order(id).map(Order::from).orElse(null), clock.instant());
    }
    public Candidates candidates(long id, AnalysisQuery.Pagination filter) {
        var scope = scope();
        var r = recommendation(id);
        var a = repository.analysis(r.getAnalysisRunId());
        if (a == null) throw new AnalysisNotFoundException();
        var selected = repository.selectedCandidate(r, a).orElse(null);
        var universe = universe(r, selected);
        if (selected == null)
            return new Candidates(scope, universe, List.of(), filter.page(), filter.size(), 0, 0, clock.instant());
        var rows = repository.candidates(selected.getAnalysisRunId(), filter).stream().map(this::candidate).toList();
        long total = repository.candidateCount(selected.getAnalysisRunId());
        return new Candidates(scope, universe, rows, filter.page(), filter.size(), total,
                pages(total, filter.size()), clock.instant());
    }
    private RecommendationRecord recommendation(long id) {
        if (id <= 0) throw new IllegalArgumentException("Invalid recommendation ID");
        return repository.recommendation(id).orElseThrow(AnalysisNotFoundException::new);
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
}
