package io.github.suover.cryptotradingbot.global.security;

import org.springframework.security.core.Authentication;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class AdminAuthController {
    // Materialize the deferred, masked token; never expose the session ID or password hash.
    @GetMapping("/api/v1/auth/csrf")
    public CsrfResponse csrf(CsrfToken token) { return new CsrfResponse(token.getHeaderName(), token.getToken()); }

    @GetMapping("/api/v1/auth/me")
    public AdminResponse me(Authentication authentication) { return new AdminResponse(authentication.getName()); }

    public record CsrfResponse(String headerName, String token) {}
    public record AdminResponse(String username) {}
}
