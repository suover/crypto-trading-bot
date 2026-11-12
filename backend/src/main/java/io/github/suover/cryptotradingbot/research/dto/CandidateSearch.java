package io.github.suover.cryptotradingbot.research.dto;

import java.time.Instant;

public record CandidateSearch(String keyword, Instant registeredFrom, Instant registeredTo,
                              Boolean shadowEnrolled, int page, int size, String sort) {
    public CandidateSearch {
        if (keyword != null) {
            keyword = keyword.strip();
            if (keyword.length() > 80) throw new IllegalArgumentException("Keyword exceeds 80 characters");
        }
        if (registeredFrom != null && registeredTo != null && registeredFrom.isAfter(registeredTo)) {
            throw new IllegalArgumentException("registeredFrom must not be after registeredTo");
        }
        validatePage(page, size);
        if (sort == null) sort = "registeredAt,desc";
        if (!sort.matches("(registeredAt|id|policyName),(asc|desc)")) {
            throw new IllegalArgumentException("Unsupported candidate sort");
        }
    }
    public static void validatePage(int page, int size) {
        if (page < 0 || size < 1 || size > 100) throw new IllegalArgumentException("Invalid page or size");
    }
}
