package io.github.suover.cryptotradingbot.global.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties("backend")
public record BackendProperties(String exchange, String quoteAsset) {
    public BackendProperties {
        if (exchange == null || !exchange.matches("[A-Z0-9]{1,30}")
                || quoteAsset == null || !quoteAsset.matches("[A-Z0-9]{1,20}")) {
            throw new IllegalArgumentException("Invalid exchange or quote asset");
        }
    }
}
