package io.github.suover.cryptotradingbot.research.entity;

import jakarta.persistence.*;
import org.hibernate.annotations.Immutable;

@Entity
@Table(name = "users")
@Immutable
public class TradingUser {
    @Id
    @Column(name = "id")
    private Long id;
    @Column(name = "is_active")
    private Boolean isActive;

    protected TradingUser() {}

    public Long getId() { return id; }
    public Boolean getIsActive() { return isActive; }
}
