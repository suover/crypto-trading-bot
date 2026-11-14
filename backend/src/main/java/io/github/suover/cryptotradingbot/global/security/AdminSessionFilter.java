package io.github.suover.cryptotradingbot.global.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;
import org.springframework.http.HttpMethod;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.servlet.util.matcher.PathPatternRequestMatcher;
import org.springframework.web.filter.OncePerRequestFilter;
import tools.jackson.databind.ObjectMapper;

/** Single-instance guard. Never trust client-supplied forwarded IP headers. */
final class AdminSessionFilter extends OncePerRequestFilter {
    static final String LOGIN_PATH = "/api/v1/auth/login";
    static final String STARTED_AT = AdminSessionFilter.class.getName() + ".startedAt";
    static final long MAX_SESSION_AGE = Duration.ofHours(8).toMillis();
    static final long LOGIN_WINDOW = Duration.ofMinutes(1).toMillis();
    static final int LOGIN_ATTEMPTS = 10;
    private static final PathPatternRequestMatcher LOGIN_REQUEST =
            PathPatternRequestMatcher.pathPattern(HttpMethod.POST, LOGIN_PATH);
    private static final int MAX_CLIENTS = 4096;
    private final Map<String, Window> attempts = new HashMap<>();
    private final ObjectMapper mapper;
    private final Clock clock;

    AdminSessionFilter(ObjectMapper mapper) { this(mapper, Clock.systemUTC()); }
    AdminSessionFilter(ObjectMapper mapper, Clock clock) { this.mapper = mapper; this.clock = clock; }

    @Override protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                            FilterChain chain) throws ServletException, IOException {
        var session = request.getSession(false);
        if (session != null && session.getAttribute(STARTED_AT) instanceof Long started
                && clock.millis() - started >= MAX_SESSION_AGE) {
            session.invalidate();
            SecurityContextHolder.clearContext();
        }
        if (LOGIN_REQUEST.matches(request)) {
            if (!allow(request.getRemoteAddr())) {
                response.setHeader("Retry-After", "60");
                SecurityConfiguration.writeError(mapper, response, 429, "TOO_MANY_ATTEMPTS", "Try again later");
                return;
            }
            String username = request.getParameter("username"), password = request.getParameter("password");
            if (username == null || username.isBlank() || username.length() > 128
                    || password == null || password.isEmpty() || password.getBytes(StandardCharsets.UTF_8).length > 72) {
                SecurityConfiguration.writeError(mapper, response, 401, "UNAUTHENTICATED", "Invalid credentials");
                return;
            }
        }
        chain.doFilter(request, response);
    }

    private synchronized boolean allow(String client) {
        long now = clock.millis();
        attempts.entrySet().removeIf(entry -> now - entry.getValue().started >= LOGIN_WINDOW);
        Window window = attempts.get(client);
        if (window == null) {
            if (attempts.size() >= MAX_CLIENTS) return false;
            window = new Window(now);
            attempts.put(client, window);
        }
        return ++window.count <= LOGIN_ATTEMPTS;
    }
    private static final class Window {
        private final long started;
        private int count;
        Window(long started) { this.started = started; }
    }
}
