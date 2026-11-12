package io.github.suover.cryptotradingbot.research.service;

public final class ResearchNotFoundException extends RuntimeException {
    public ResearchNotFoundException() { super("Research candidate not found"); }
}
