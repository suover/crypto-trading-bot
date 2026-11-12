package io.github.suover.cryptotradingbot;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import io.github.suover.cryptotradingbot.global.config.BackendProperties;

@SpringBootApplication
@EnableConfigurationProperties(BackendProperties.class)
public class CryptoTradingBotApplication {
    public static void main(String[] args) {
        SpringApplication.run(CryptoTradingBotApplication.class, args);
    }
}
