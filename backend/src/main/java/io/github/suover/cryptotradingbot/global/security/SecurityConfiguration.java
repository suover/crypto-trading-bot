package io.github.suover.cryptotradingbot.global.security;

import io.github.suover.cryptotradingbot.global.exception.ApiError;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.security.web.servlet.util.matcher.PathPatternRequestMatcher;
import tools.jackson.databind.ObjectMapper;

@Configuration
public class SecurityConfiguration {
    @Bean BCryptPasswordEncoder passwordEncoder() { return new BCryptPasswordEncoder(); }

    @Bean SecurityFilterChain security(HttpSecurity http, ObjectMapper mapper) throws Exception {
        // Only authentication changes session state. Business APIs remain SELECT-only.
        return http.csrf(csrf -> {})
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.IF_REQUIRED)
                        .sessionFixation(fixation -> fixation.changeSessionId()))
                .requestCache(cache -> cache.disable())
                .addFilterBefore(new AdminSessionFilter(mapper), UsernamePasswordAuthenticationFilter.class)
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers(HttpMethod.GET, "/api/v1/auth/csrf").permitAll()
                        .requestMatchers(HttpMethod.POST, AdminSessionFilter.LOGIN_PATH).permitAll()
                        .requestMatchers(HttpMethod.GET, "/api/v1/auth/me").hasRole("ADMIN")
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
                .formLogin(login -> login.loginPage("/login").loginProcessingUrl(AdminSessionFilter.LOGIN_PATH)
                        .successHandler((request, response, authentication) -> {
                            request.getSession().setAttribute(AdminSessionFilter.STARTED_AT, System.currentTimeMillis());
                            response.setStatus(204);
                        })
                        .failureHandler((request, response, exception) ->
                                writeError(mapper, response, 401, "UNAUTHENTICATED", "Invalid credentials")))
                .logout(logout -> logout.logoutRequestMatcher(PathPatternRequestMatcher.withDefaults()
                                .matcher(HttpMethod.POST, "/api/v1/auth/logout"))
                        .invalidateHttpSession(true).clearAuthentication(true).deleteCookies("JSESSIONID")
                        .logoutSuccessHandler((request, response, authentication) -> response.setStatus(204)))
                .exceptionHandling(errors -> errors
                        .authenticationEntryPoint((request, response, exception) ->
                                writeError(mapper, response, 401, "UNAUTHENTICATED", "Authentication required"))
                        .accessDeniedHandler((request, response, exception) ->
                                writeError(mapper, response, 403, "FORBIDDEN", "Access denied")))
                .build();
    }

    static void writeError(ObjectMapper mapper, HttpServletResponse response,
                                   int status, String code, String message) throws IOException {
        response.setStatus(status);
        response.setContentType("application/json");
        response.getWriter().write(mapper.writeValueAsString(ApiError.of(code, message)));
    }
}
