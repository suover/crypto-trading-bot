package io.github.suover.cryptotradingbot.global.config;

import org.springframework.beans.factory.config.BeanFactoryPostProcessor;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.env.Environment;

@Configuration
public class RequiredDatabaseConfiguration {
    @Bean
    static BeanFactoryPostProcessor requiredDatabaseSettings(Environment environment) {
        return beanFactory -> {
            String url = environment.getProperty("spring.datasource.url", "");
            String username = environment.getProperty("spring.datasource.username", "");
            String password = environment.getProperty("spring.datasource.password", "");
            if (!url.startsWith("jdbc:postgresql://") || username.isBlank() || password.isBlank()
                    || url.matches("(?i).*([?&])(user|password)=.*")) {
                // A generic error avoids logging credentials, including a malformed URL.
                throw new IllegalArgumentException("Explicit backend PostgreSQL URL and separate credentials are required");
            }
        };
    }
}
