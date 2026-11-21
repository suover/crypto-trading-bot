package io.github.suover.cryptotradingbot.portfolio.entity;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.Instant;
import org.hibernate.annotations.Immutable;

@Entity
@Table(name = "order_fills")
@Immutable
public class OrderFillRecord {
    @Id private Long id;
    @Column(name = "order_log_id") private Long orderLogId;
    @Column(precision = 30, scale = 10) private BigDecimal price;
    @Column(precision = 30, scale = 10) private BigDecimal volume;
    @Column(name = "funds_krw", precision = 30, scale = 10) private BigDecimal fundsKrw;
    @Column(name = "created_at") private Instant recordedAt;

    protected OrderFillRecord() {}

    public Long getId() { return id; }
    public BigDecimal getPrice() { return price; }
    public BigDecimal getVolume() { return volume; }
    public BigDecimal getFundsKrw() { return fundsKrw; }
    public Instant getRecordedAt() { return recordedAt; }
}
