package io.github.suover.cryptotradingbot.global.security;

import io.github.suover.cryptotradingbot.global.config.BackendProperties;
import io.github.suover.cryptotradingbot.global.exception.ApiError;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.userdetails.User;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.provisioning.InMemoryUserDetailsManager;
import org.springframework.security.web.SecurityFilterChain;
import tools.jackson.databind.ObjectMapper;

@Configuration
public class SecurityConfiguration {
    @Bean BCryptPasswordEncoder passwordEncoder() { return new BCryptPasswordEncoder(); }

    @Bean UserDetailsService administrators(BackendProperties properties) {
        return new InMemoryUserDetailsManager(User.withUsername(properties.adminUsername())
                .password(properties.adminPasswordHash()).roles("ADMIN").build());
    }

    @Bean SecurityFilterChain security(HttpSecurity http, ObjectMapper mapper) throws Exception {
        // No state-changing endpoints or session/cookie authentication exist.
        // Basic authentication must remain loopback-only or behind TLS.
        return http.csrf(csrf -> csrf.disable())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .requestCache(cache -> cache.disable())
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers(HttpMethod.GET, "/api/v1/research/summary",
                                "/api/v1/research/candidates", "/api/v1/research/candidates/{id}",
                                "/api/v1/research/snapshots", "/api/v1/research/automation-status")
                        .hasRole("ADMIN")
                        .requestMatchers(HttpMethod.GET, "/api/v1/overview/summary",
                                "/api/v1/overview/positions", "/api/v1/overview/performance",
                                "/api/v1/overview/recommendations")
                        .hasRole("ADMIN")
                        .requestMatchers(HttpMethod.GET, "/api/v1/ai-trade-analysis/recommendations",
                                "/api/v1/ai-trade-analysis/recommendations/{id}",
                                "/api/v1/ai-trade-analysis/recommendations/{id}/candidates")
                        .hasRole("ADMIN")
                        .anyRequest().denyAll())
                .httpBasic(basic -> basic.authenticationEntryPoint((request, response, exception) -> {
                    response.setHeader("WWW-Authenticate", "Basic realm=\"research\"");
                    writeError(mapper, response, 401, "UNAUTHENTICATED", "Authentication required");
                }))
                .exceptionHandling(errors -> errors.accessDeniedHandler((request, response, exception) ->
                        writeError(mapper, response, 403, "FORBIDDEN", "Access denied")))
                .build();
    }

    private static void writeError(ObjectMapper mapper, HttpServletResponse response,
                                   int status, String code, String message) throws IOException {
        response.setStatus(status);
        response.setContentType("application/json");
        response.getWriter().write(mapper.writeValueAsString(ApiError.of(code, message)));
    }
}
