package io.github.suover.cryptotradingbot.global.config;

import java.time.Clock;
import jakarta.persistence.EntityManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import com.querydsl.jpa.impl.JPAQueryFactory;

@Configuration
public class PersistenceConfiguration {
    @Bean JPAQueryFactory queryFactory(EntityManager entityManager) {
        return new JPAQueryFactory(entityManager);
    }
    @Bean Clock clock() { return Clock.systemUTC(); }
}
