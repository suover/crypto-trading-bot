package io.github.suover.cryptotradingbot.research.repository;

import io.github.suover.cryptotradingbot.research.entity.TradingUser;
import java.util.Optional;
import org.springframework.data.repository.Repository;

// Intentionally omit all write methods, including save/delete/flush.
public interface TradingUserRepository extends Repository<TradingUser, Long> {
    Optional<TradingUser> findById(Long id);
}
