package io.github.suover.cryptotradingbot.overview.dto;

import java.time.Instant;
import java.util.Set;

public final class OverviewQuery {
    private OverviewQuery() {}
    // Bound work on the existing indexes without adding schema or silently truncating pages.
    public static final long MAX_OFFSET = 1_000_000;
    public static void validatePage(int page, int size, int maximum) {
        if (page < 0 || size < 1 || size > maximum || (long) page * size > MAX_OFFSET) {
            throw new IllegalArgumentException("Invalid page range");
        }
    }
    public record Performance(Instant from, Instant to, int page, int size) {
        public Performance {
            validatePage(page, size, 200);
            if (from != null && to != null && from.isAfter(to)) {
                throw new IllegalArgumentException("Invalid period");
            }
        }
    }
    public record Recommendations(String action, int page, int size) {
        public Recommendations {
            validatePage(page, size, 50);
            if (action != null && !Set.of("BUY", "SELL", "HOLD").contains(action)) {
                throw new IllegalArgumentException("Invalid action");
            }
        }
    }
}
