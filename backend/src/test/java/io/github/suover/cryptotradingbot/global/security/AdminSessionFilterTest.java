package io.github.suover.cryptotradingbot.global.security;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import java.time.Clock;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import tools.jackson.databind.ObjectMapper;

class AdminSessionFilterTest {
    @Test void loginWindowResetsWithoutSleepAndClientsRemainIndependent() throws Exception {
        Clock clock = mock(Clock.class);
        when(clock.millis()).thenReturn(0L);
        var filter = new AdminSessionFilter(new ObjectMapper(), clock);
        AtomicInteger accepted = new AtomicInteger();
        for (int i = 0; i < 11; i++) {
            var response = new MockHttpServletResponse();
            filter.doFilter(request("192.0.2.1", "fixture-only"), response, (req, res) -> accepted.incrementAndGet());
            assertThat(response.getStatus()).isEqualTo(i < 10 ? 200 : 429);
        }
        var encodedLogin = request("192.0.2.1", "fixture-only");
        encodedLogin.setRequestURI("/api/v1/auth/lo%67in");
        var encodedResponse = new MockHttpServletResponse();
        filter.doFilter(encodedLogin, encodedResponse, (req, res) -> fail("Equivalent login path must use the same limit"));
        assertThat(encodedResponse.getStatus()).isEqualTo(429);
        filter.doFilter(request("192.0.2.2", "fixture-only"), new MockHttpServletResponse(), (req, res) -> accepted.incrementAndGet());
        when(clock.millis()).thenReturn(AdminSessionFilter.LOGIN_WINDOW);
        filter.doFilter(request("192.0.2.1", "fixture-only"), new MockHttpServletResponse(), (req, res) -> accepted.incrementAndGet());
        assertThat(accepted.get()).isEqualTo(12);
    }
    @Test void bcryptByteLimitAndMissingPasswordAreRejectedBeforeAuthentication() throws Exception {
        var filter = new AdminSessionFilter(new ObjectMapper());
        for (String password : new String[]{"", "a".repeat(73), "한".repeat(25)}) {
            var response = new MockHttpServletResponse();
            filter.doFilter(request("192.0.2.1", password), response, (req, res) -> fail("Must not authenticate invalid input"));
            assertThat(response.getStatus()).isEqualTo(401);
            assertThat(response.getContentAsString()).contains("UNAUTHENTICATED");
        }
    }
    private MockHttpServletRequest request(String client, String password) {
        var request = new MockHttpServletRequest("POST", "/api/v1/auth/login");
        request.setRemoteAddr(client);
        request.setParameter("username", "fixture-admin");
        request.setParameter("password", password);
        return request;
    }
}
