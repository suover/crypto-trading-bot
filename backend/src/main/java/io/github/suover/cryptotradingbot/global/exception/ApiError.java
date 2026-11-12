package io.github.suover.cryptotradingbot.global.exception;

import java.time.Instant;

public record ApiError(String code, String message, Instant generatedAt) {
    public static ApiError of(String code, String message) {
        return new ApiError(code, message, Instant.now());
    }
}
