package io.github.suover.cryptotradingbot;

import static org.assertj.core.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;

import io.github.suover.cryptotradingbot.global.security.AdminAuthController;
import io.github.suover.cryptotradingbot.global.security.SecurityConfiguration;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.RequestPostProcessor;
import tools.jackson.databind.ObjectMapper;

@WebMvcTest(controllers = AdminAuthController.class, properties = {
        "backend.trading-user-id=1", "backend.exchange=UPBIT", "backend.quote-asset=KRW",
        "backend.admin-username=fixture-admin"
})
@Import(SecurityConfiguration.class)
class AdminSessionTest {
    @Autowired MockMvc mvc;
    @Autowired ObjectMapper mapper;
    private static int client;
    private RequestPostProcessor address;
    @DynamicPropertySource static void credentials(DynamicPropertyRegistry registry) {
        registry.add("backend.admin-password-hash", () -> new BCryptPasswordEncoder().encode("fixture-only-password"));
    }
    @BeforeEach void client() {
        String ip = "192.0.2." + ++client;
        address = request -> { request.setRemoteAddr(ip); return request; };
    }
    private record Token(MockHttpSession session, String value) {}
    private Token token(MockHttpSession session) throws Exception {
        var request = get("/api/v1/auth/csrf");
        if (session != null) request.session(session);
        var result = mvc.perform(request).andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", org.hamcrest.Matchers.containsString("no-store")))
                .andExpect(jsonPath("$.headerName").value("X-CSRF-TOKEN")).andReturn();
        return new Token((MockHttpSession) result.getRequest().getSession(),
                mapper.readTree(result.getResponse().getContentAsString()).get("token").asText());
    }
    private MockHttpSession login(Token csrf) throws Exception {
        return (MockHttpSession) mvc.perform(post("/api/v1/auth/login").with(address)
                .with(request -> { request.setRequestedSessionId(csrf.session.getId());
                    request.setRequestedSessionIdValid(true); return request; })
                .session(csrf.session).header("X-CSRF-TOKEN", csrf.value)
                .param("username", "fixture-admin").param("password", "fixture-only-password"))
                .andExpect(status().isNoContent()).andExpect(header().doesNotExist("Location"))
                .andReturn().getRequest().getSession();
    }
    @Test void loginRotatesSessionPersistsAuthenticationAndLogoutRevokesIt() throws Exception {
        Token csrf = token(null);
        String before = csrf.session.getId();
        MockHttpSession session = login(csrf);
        assertThat(session.getId()).isNotEqualTo(before);
        for (int i = 0; i < 2; i++) {
            mvc.perform(get("/api/v1/auth/me").session(session)).andExpect(status().isOk())
                    .andExpect(jsonPath("$.username").value("fixture-admin"))
                    .andExpect(jsonPath("$.password").doesNotExist()).andExpect(jsonPath("$.authorities").doesNotExist());
        }
        // Login invalidates the old CSRF token; a fresh one is required for logout.
        mvc.perform(post("/api/v1/auth/logout").session(session).header("X-CSRF-TOKEN", csrf.value))
                .andExpect(status().isForbidden());
        Token fresh = token(session);
        mvc.perform(post("/api/v1/auth/logout").session(session).header("X-CSRF-TOKEN", fresh.value))
                .andExpect(status().isNoContent()).andExpect(cookie().maxAge("JSESSIONID", 0));
        assertThat(session.isInvalid()).isTrue();
        mvc.perform(get("/api/v1/auth/me").cookie(new jakarta.servlet.http.Cookie("JSESSIONID", before)))
                .andExpect(status().isUnauthorized());
    }
    @Test void missingAndInvalidCsrfCannotLoginOrLogout() throws Exception {
        mvc.perform(post("/api/v1/auth/login").param("username", "fixture-admin").param("password", "fixture-only-password"))
                .andExpect(status().isForbidden());
        mvc.perform(post("/api/v1/auth/login").with(csrf().useInvalidToken())).andExpect(status().isForbidden());
        var session = login(token(null));
        mvc.perform(post("/api/v1/auth/logout").session(session)).andExpect(status().isForbidden());
        mvc.perform(get("/api/v1/auth/logout").session(session)).andExpect(status().isForbidden());
        mvc.perform(get("/api/v1/auth/me").session(session)).andExpect(status().isOk());
    }
    @Test void wrongPasswordAndUnknownUserHaveSameSanitizedResponse() throws Exception {
        for (String username : new String[]{"fixture-admin", "missing"}) {
            mvc.perform(post("/api/v1/auth/login").with(address).with(csrf())
                    .param("username", username).param("password", "do-not-echo-this"))
                    .andExpect(status().isUnauthorized()).andExpect(jsonPath("$.code").value("UNAUTHENTICATED"))
                    .andExpect(content().string(org.hamcrest.Matchers.not(org.hamcrest.Matchers.containsString("do-not-echo-this"))));
        }
        mvc.perform(get("/api/v1/auth/me")).andExpect(status().isUnauthorized())
                .andExpect(header().doesNotExist("WWW-Authenticate"));
        mvc.perform(get("/api/v1/auth/me").with(httpBasic("fixture-admin", "fixture-only-password")))
                .andExpect(status().isUnauthorized());
    }
    @Test void absoluteSessionExpiryFailsClosed() throws Exception {
        var session = login(token(null));
        session.setAttribute("io.github.suover.cryptotradingbot.global.security.AdminSessionFilter.startedAt", 0L);
        mvc.perform(get("/api/v1/auth/me").session(session)).andExpect(status().isUnauthorized());
        assertThat(session.isInvalid()).isTrue();
    }
    @Test void repeatedAttemptsAreLimitedEvenWithSpoofedForwardedHeaders() throws Exception {
        for (int i = 0; i < 10; i++) {
            mvc.perform(post("/api/v1/auth/login").with(address).with(csrf())
                    .param("username", "missing").param("password", "wrong"))
                    .andExpect(status().isUnauthorized());
        }
        mvc.perform(post("/api/v1/auth/login").with(address).with(csrf()).header("X-Forwarded-For", "198.51.100.5"))
                .andExpect(status().isTooManyRequests()).andExpect(header().string("Retry-After", "60"));
    }
    @ParameterizedTest @ValueSource(strings = {"/api/v1/orders", "/api/v1/research/candidates", "/api/v1/approvals", "/api/v1/auth/register"})
    void sessionAndCsrfNeverAuthorizeBusinessWrites(String path) throws Exception {
        var session = login(token(null));
        mvc.perform(post(path).session(session).with(csrf())).andExpect(status().isForbidden());
    }
}
