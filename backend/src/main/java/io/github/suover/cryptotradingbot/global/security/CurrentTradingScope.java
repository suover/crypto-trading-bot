package io.github.suover.cryptotradingbot.global.security;

import io.github.suover.cryptotradingbot.global.config.BackendProperties;
import io.github.suover.cryptotradingbot.research.entity.TradingUser;
import io.github.suover.cryptotradingbot.research.repository.TradingUserRepository;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;

@Component
public class CurrentTradingScope {
    public record Scope(long tradingUserId, String exchange, String quoteAsset) {}

    private final DashboardAccounts accounts;
    private final TradingUserRepository users;
    private final BackendProperties properties;

    public CurrentTradingScope(DashboardAccounts accounts, TradingUserRepository users, BackendProperties properties) {
        this.accounts = accounts;
        this.users = users;
        this.properties = properties;
    }

    public Scope resolve() {
        var authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication == null || !authentication.isAuthenticated()
                || !(authentication.getPrincipal() instanceof DashboardPrincipal principal)) {
            throw new AccessDeniedException("Dashboard account is unavailable");
        }
        long userId = accounts.activeTradingUser(principal)
                .orElseThrow(() -> new AccessDeniedException("Dashboard account or link changed"));
        if (!users.findById(userId).map(TradingUser::getIsActive).orElse(false)) {
            throw new AccessDeniedException("Linked trading user is unavailable");
        }
        return new Scope(userId, properties.exchange(), properties.quoteAsset());
    }
}
