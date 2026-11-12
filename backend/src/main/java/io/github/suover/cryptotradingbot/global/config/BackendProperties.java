package io.github.suover.cryptotradingbot.global.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

// Do not use a record: generated toString() must not disclose the password hash.
@ConfigurationProperties("backend")
public final class BackendProperties {
    private final long tradingUserId;
    private final String exchange;
    private final String quoteAsset;
    private final String adminUsername;
    private final String adminPasswordHash;

    public BackendProperties(long tradingUserId, String exchange, String quoteAsset,
                             String adminUsername, String adminPasswordHash) {
        if (tradingUserId <= 0 || exchange == null || !exchange.matches("[A-Z0-9]{1,30}")
                || quoteAsset == null || !quoteAsset.matches("[A-Z0-9]{1,20}")
                || adminUsername == null || adminUsername.isBlank()
                || adminUsername.contains(":") || adminPasswordHash == null
                || !adminPasswordHash.matches("\\$2[aby]\\$(1[0-6])\\$[./A-Za-z0-9]{53}")) {
            throw new IllegalArgumentException("Required backend scope or administrator configuration is invalid");
        }
        this.tradingUserId = tradingUserId;
        this.exchange = exchange;
        this.quoteAsset = quoteAsset;
        this.adminUsername = adminUsername;
        this.adminPasswordHash = adminPasswordHash;
    }
    public long tradingUserId() { return tradingUserId; }
    public String exchange() { return exchange; }
    public String quoteAsset() { return quoteAsset; }
    public String adminUsername() { return adminUsername; }
    public String adminPasswordHash() { return adminPasswordHash; }
}
