package io.github.suover.cryptotradingbot.global.security;

import java.util.List;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.userdetails.User;

final class DashboardPrincipal extends User {
    private final long accountId;
    private final long tradingUserId;

    DashboardPrincipal(long accountId, String username, String hash, boolean enabled, long tradingUserId) {
        super(username, hash, enabled, true, true, true, List.of(new SimpleGrantedAuthority("ROLE_ADMIN")));
        this.accountId = accountId;
        this.tradingUserId = tradingUserId;
    }

    long accountId() { return accountId; }
    long tradingUserId() { return tradingUserId; }
}
