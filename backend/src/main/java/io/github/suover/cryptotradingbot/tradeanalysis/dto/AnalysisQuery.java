package io.github.suover.cryptotradingbot.tradeanalysis.dto;

import java.time.Instant;
import java.util.Set;

public final class AnalysisQuery {
    private AnalysisQuery() {}
    public static void validatePage(int page, int size) {
        if (page < 0 || size < 1 || size > 100 || (long) page * size > 1_000_000)
            throw new IllegalArgumentException("Invalid page range");
    }
    public record Search(Instant from, Instant to, String market, String action, int page, int size) {
        public Search {
            validatePage(page, size);
            if (from != null && to != null && from.isAfter(to))
                throw new IllegalArgumentException("Invalid period");
            if (action != null && !Set.of("BUY", "SELL", "HOLD").contains(action))
                throw new IllegalArgumentException("Invalid action");
            if (market != null && (market.length() > 30 || !market.matches("[A-Z0-9]+-[A-Z0-9]+")))
                throw new IllegalArgumentException("Invalid market");
        }
    }
    public record Pagination(int page, int size) {
        public Pagination { validatePage(page, size); }
    }
}
