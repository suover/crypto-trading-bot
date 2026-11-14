package io.github.suover.cryptotradingbot.overview.service;

import io.github.suover.cryptotradingbot.global.security.CurrentTradingScope;
import io.github.suover.cryptotradingbot.overview.dto.OverviewQuery;
import io.github.suover.cryptotradingbot.overview.dto.OverviewResponses.*;
import io.github.suover.cryptotradingbot.overview.repository.OverviewReadRepository;
import java.time.Clock;
import java.util.List;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
public class OverviewReadService {
    private final OverviewReadRepository repository;
    private final CurrentTradingScope tradingScope;
    private final Clock clock;
    public OverviewReadService(OverviewReadRepository repository, CurrentTradingScope tradingScope, Clock clock) {
        this.repository = repository;
        this.tradingScope = tradingScope;
        this.clock = clock;
    }
    public Summary summary() {
        var scope = tradingScope.resolve();
        var latest = repository.latestPortfolio(scope).orElse(null);
        var performance = latest == null ? null
                : repository.performance(scope, latest.getId()).map(Performance::from).orElse(null);
        return new Summary(responseScope(scope), latest == null ? null : Portfolio.from(latest), performance,
                repository.botPnl(scope).map(BotPnl::from).orElse(null), clock.instant());
    }
    public Positions positions() {
        var scope = tradingScope.resolve();
        var latest = repository.latestPortfolio(scope).orElse(null);
        return new Positions(responseScope(scope), latest == null ? null : Portfolio.from(latest),
                latest == null ? List.of() : repository.positions(scope, latest.getId()).stream().map(Position::from).toList(),
                clock.instant());
    }
    public Page<Performance> performance(OverviewQuery.Performance filter) {
        var scope = tradingScope.resolve();
        return page(responseScope(scope), repository.performanceHistory(scope, filter).stream().map(Performance::from).toList(),
                filter.page(), filter.size(), repository.performanceCount(scope, filter));
    }
    public Page<Recommendation> recommendations(OverviewQuery.Recommendations filter) {
        var scope = tradingScope.resolve();
        return page(responseScope(scope), repository.recommendations(scope, filter).stream().map(Recommendation::from).toList(),
                filter.page(), filter.size(), repository.recommendationCount(scope, filter));
    }
    private <T> Page<T> page(Scope scope, List<T> items, int page, int size, long total) {
        return new Page<>(scope, items, page, size, total,
                total / size + (total % size == 0 ? 0 : 1), clock.instant());
    }
    private static Scope responseScope(CurrentTradingScope.Scope scope) {
        return new Scope(Long.toString(scope.tradingUserId()), scope.exchange(), scope.quoteAsset());
    }
}
