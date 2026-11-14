package io.github.suover.cryptotradingbot;

import static org.assertj.core.api.Assertions.*;
import io.github.suover.cryptotradingbot.global.config.*;
import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;

class BackendConfigurationTest {
    @Configuration(proxyBeanMethods = false)
    @EnableConfigurationProperties(BackendProperties.class)
    static class PropertyConfiguration {}

    @Test void marketConfigBindsWithoutAccountOrTradingUserId() {
        new ApplicationContextRunner().withUserConfiguration(PropertyConfiguration.class)
                .withPropertyValues("backend.exchange=UPBIT", "backend.quote-asset=KRW")
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    assertThat(context.getBean(BackendProperties.class).exchange()).isEqualTo("UPBIT");
                });
    }
    @Test void databaseCredentialsCannotBeEmptyOrUseFallbackDatabase() {
        new ApplicationContextRunner().withUserConfiguration(RequiredDatabaseConfiguration.class)
                .run(context -> assertThat(context).hasFailed());
        new ApplicationContextRunner().withUserConfiguration(RequiredDatabaseConfiguration.class)
                .withPropertyValues("spring.datasource.url=jdbc:postgresql://127.0.0.1/test",
                        "spring.datasource.username=fixture", "spring.datasource.password=")
                .run(context -> assertThat(context).hasFailed());
    }
    @Test void validDatabaseConfigAndCredentialBearingUrl() {
        var runner = new ApplicationContextRunner().withUserConfiguration(RequiredDatabaseConfiguration.class)
                .withPropertyValues("spring.datasource.username=fixture",
                        "spring.datasource.password=fixture-only");
        runner.withPropertyValues("spring.datasource.url=jdbc:postgresql://127.0.0.1/test")
                .run(context -> assertThat(context).hasNotFailed());
        runner.withPropertyValues("spring.datasource.url=jdbc:postgresql://127.0.0.1/test?password=fixture-only")
                .run(context -> assertThat(context).hasFailed());
    }
}
