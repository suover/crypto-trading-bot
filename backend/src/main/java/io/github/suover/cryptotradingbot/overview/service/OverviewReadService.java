package io.github.suover.cryptotradingbot.overview.service;

import io.github.suover.cryptotradingbot.global.config.BackendProperties;
import io.github.suover.cryptotradingbot.overview.dto.OverviewQuery;
import io.github.suover.cryptotradingbot.overview.dto.OverviewResponses.*;
import io.github.suover.cryptotradingbot.overview.repository.OverviewReadRepository;
import io.github.suover.cryptotradingbot.research.entity.TradingUser;
import io.github.suover.cryptotradingbot.research.repository.TradingUserRepository;
import java.time.Clock;
import java.util.List;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
public class OverviewReadService {
    private final OverviewReadRepository repository;
    private final TradingUserRepository users;
    private final BackendProperties properties;
    private final Clock clock;
    public OverviewReadService(OverviewReadRepository repository, TradingUserRepository users,
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
        return new Scope(Long.toString(properties.tradingUserId()), properties.exchange(), properties.quoteAsset());
    }
    public Summary summary() {
        var scope = validateScope();
        var latest = repository.latestPortfolio().orElse(null);
        var performance = latest == null ? null
                : repository.performance(latest.getId()).map(Performance::from).orElse(null);
        return new Summary(scope, latest == null ? null : Portfolio.from(latest), performance,
                repository.botPnl().map(BotPnl::from).orElse(null), clock.instant());
    }
    public Positions positions() {
        var scope = validateScope();
        var latest = repository.latestPortfolio().orElse(null);
        return new Positions(scope, latest == null ? null : Portfolio.from(latest),
                latest == null ? List.of() : repository.positions(latest.getId()).stream().map(Position::from).toList(),
                clock.instant());
    }
    public Page<Performance> performance(OverviewQuery.Performance filter) {
        var scope = validateScope();
        return page(scope, repository.performanceHistory(filter).stream().map(Performance::from).toList(),
                filter.page(), filter.size(), repository.performanceCount(filter));
    }
    public Page<Recommendation> recommendations(OverviewQuery.Recommendations filter) {
        var scope = validateScope();
        return page(scope, repository.recommendations(filter).stream().map(Recommendation::from).toList(),
                filter.page(), filter.size(), repository.recommendationCount(filter));
    }
    private <T> Page<T> page(Scope scope, List<T> items, int page, int size, long total) {
        return new Page<>(scope, items, page, size, total,
                total / size + (total % size == 0 ? 0 : 1), clock.instant());
    }
}
