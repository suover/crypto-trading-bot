package io.github.suover.cryptotradingbot.global.security;

import java.util.Optional;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.stereotype.Service;

@Service
public class DashboardAccounts implements UserDetailsService {
    private final JdbcTemplate jdbc;

    public DashboardAccounts(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
        jdbc.execute("SELECT id, username, password_hash, enabled, trading_user_id FROM auth.accounts WHERE false");
    }

    @Override
    public UserDetails loadUserByUsername(String username) {
        return jdbc.query("""
                SELECT id, username, password_hash, enabled, trading_user_id
                FROM auth.accounts WHERE username = ?
                """, (rs, row) -> new DashboardPrincipal(rs.getLong("id"), rs.getString("username"),
                        rs.getString("password_hash"), rs.getBoolean("enabled"), rs.getLong("trading_user_id")), username)
                .stream().findFirst().orElseThrow(() -> new UsernameNotFoundException("Unknown dashboard account"));
    }

    Optional<Long> activeTradingUser(DashboardPrincipal principal) {
        return jdbc.query("""
                SELECT trading_user_id FROM auth.accounts
                WHERE id = ? AND username = ? AND enabled AND trading_user_id = ?
                """, (rs, row) -> rs.getLong(1),
                principal.accountId(), principal.getUsername(), principal.tradingUserId()).stream().findFirst();
    }
}
