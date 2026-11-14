package io.github.suover.cryptotradingbot;

import static org.assertj.core.api.Assertions.*;
import io.github.suover.cryptotradingbot.support.PostgresFixtureTest;
import java.net.CookieManager;
import java.net.CookiePolicy;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.sql.DriverManager;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import tools.jackson.databind.ObjectMapper;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

/** Real servlet-container cookies plus SELECT-only Alembic database, without external services. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "server.servlet.session.cookie.secure=false"
})
class AdminSessionHttpTest extends PostgresFixtureTest {
    @LocalServerPort int port;
    @Autowired ObjectMapper mapper;
    @Autowired MockMvc mvc;

    @Test void authenticatedAccountDeterminesTheScopeForEveryReadArea() throws Exception {
        var first = adminSession(mvc);
        var second = adminSession(mvc, "fixture-other");
        for (String path : new String[] {"/api/v1/overview/summary", "/api/v1/research/summary",
                "/api/v1/ai-trade-analysis/recommendations"}) {
            mvc.perform(get(path).session(first)).andExpect(status().isOk())
                    .andExpect(jsonPath("$.scope.userId").value("1"));
            mvc.perform(get(path).session(second)).andExpect(status().isOk())
                    .andExpect(jsonPath("$.scope.userId").value("20"));
        }
        String recommendations = "/api/v1/ai-trade-analysis/recommendations";
        mvc.perform(get(recommendations + "/4001").session(first)).andExpect(status().isOk());
        mvc.perform(get(recommendations + "/4001").session(second)).andExpect(status().isNotFound());
        mvc.perform(get(recommendations + "/5001").session(second)).andExpect(status().isOk());
        mvc.perform(get(recommendations + "/5001").session(first)).andExpect(status().isNotFound());
        mvc.perform(get("/api/v1/research/candidates/201").session(first)).andExpect(status().isOk());
        mvc.perform(get("/api/v1/research/candidates/201").session(second)).andExpect(status().isNotFound());
        mvc.perform(get(recommendations).param("userId", "20").session(first)).andExpect(status().isBadRequest());
    }

    @Test void disablingOrRelinkingAccountDeniesAnExistingSession() throws Exception {
        var session = adminSession(mvc);
        try (var auth = DriverManager.getConnection(POSTGRES.getJdbcUrl(), POSTGRES.getUsername(), POSTGRES.getPassword());
             var sql = auth.createStatement()) {
            try {
                sql.executeUpdate("UPDATE auth.accounts SET trading_user_id = 20 WHERE username = 'fixture-admin'");
                mvc.perform(get("/api/v1/overview/summary").session(session)).andExpect(status().isForbidden());
                sql.executeUpdate("UPDATE auth.accounts SET trading_user_id = 1, enabled = false WHERE username = 'fixture-admin'");
                for (String path : new String[]{"/api/v1/auth/me", "/api/v1/overview/summary", "/api/v1/research/summary",
                        "/api/v1/ai-trade-analysis/recommendations"}) {
                    mvc.perform(get(path).session(session)).andExpect(status().isForbidden());
                }
            } finally {
                sql.executeUpdate("UPDATE auth.accounts SET trading_user_id = 1, enabled = true WHERE username = 'fixture-admin'");
            }
        }
    }

    @Test void authRuntimeRoleCannotChangeAccountRows() throws Exception {
        try (var auth = DriverManager.getConnection(POSTGRES.getJdbcUrl(), "backend_reader", "reader-fixture-only");
             var sql = auth.createStatement()) {
            assertThatThrownBy(() -> sql.executeUpdate("UPDATE auth.accounts SET enabled = false"))
                    .isInstanceOfSatisfying(java.sql.SQLException.class,
                            error -> assertThat(error.getSQLState()).isEqualTo("42501"));
            assertThatThrownBy(() -> sql.executeUpdate("UPDATE public.users SET is_active = false"))
                    .isInstanceOfSatisfying(java.sql.SQLException.class,
                            error -> assertThat(error.getSQLState()).isEqualTo("42501"));
        }
    }

    @Test void operatorCanInsertAnAccountButCannotReadOrReplaceExistingAccounts() throws Exception {
        try (var auth = DriverManager.getConnection(POSTGRES.getJdbcUrl(), "dashboard_auth_writer", "writer-fixture-only")) {
            auth.setAutoCommit(false);
            try (var insert = auth.prepareStatement("""
                    INSERT INTO auth.accounts(username, password_hash, trading_user_id) VALUES (?, ?, ?)
                    """)) {
                insert.setString(1, "operator-fixture");
                insert.setString(2, HASH);
                insert.setLong(3, 1);
                assertThat(insert.executeUpdate()).isEqualTo(1);
                assertThatThrownBy(insert::executeUpdate).isInstanceOfSatisfying(java.sql.SQLException.class,
                        error -> assertThat(error.getSQLState()).isEqualTo("23505"));
            } finally {
                auth.rollback();
            }
            try (var sql = auth.createStatement()) {
                assertThatThrownBy(() -> sql.executeQuery("SELECT username FROM auth.accounts"))
                        .isInstanceOfSatisfying(java.sql.SQLException.class,
                                error -> assertThat(error.getSQLState()).isEqualTo("42501"));
                auth.rollback();
                assertThatThrownBy(() -> sql.executeUpdate("UPDATE auth.accounts SET enabled = false"))
                        .isInstanceOfSatisfying(java.sql.SQLException.class,
                                error -> assertThat(error.getSQLState()).isEqualTo("42501"));
                auth.rollback();
                assertThatThrownBy(() -> sql.executeUpdate("UPDATE public.users SET is_active = false"))
                        .isInstanceOfSatisfying(java.sql.SQLException.class,
                                error -> assertThat(error.getSQLState()).isEqualTo("42501"));
            }
        }
    }

    @Test void accountCannotReferenceAMissingTradingUser() throws Exception {
        try (var auth = DriverManager.getConnection(POSTGRES.getJdbcUrl(), "dashboard_auth_writer", "writer-fixture-only");
             var insert = auth.prepareStatement("""
                     INSERT INTO auth.accounts(username, password_hash, trading_user_id) VALUES (?, ?, ?)
                     """)) {
            insert.setString(1, "missing-user-fixture");
            insert.setString(2, HASH);
            insert.setLong(3, Long.MAX_VALUE);
            assertThatThrownBy(insert::executeUpdate).isInstanceOfSatisfying(java.sql.SQLException.class,
                    error -> assertThat(error.getSQLState()).isEqualTo("23503"));
        }
    }

    @Test void browserCookieContractAndDatabaseReadWorkAcrossLoginAndLogout() throws Exception {
        var cookies = new CookieManager(null, CookiePolicy.ACCEPT_ALL);
        var client = HttpClient.newBuilder().cookieHandler(cookies).followRedirects(HttpClient.Redirect.NEVER).build();
        assertThat(send(client, "GET", "/api/v1/auth/me", null, null).statusCode()).isEqualTo(401);
        assertThat(send(client, "POST", "/api/v1/auth/login", null, "username=fixture-admin&password=admin-fixture-only")
                .statusCode()).isEqualTo(403);
        cookies.getCookieStore().removeAll();
        var csrf = send(client, "GET", "/api/v1/auth/csrf", null, null);
        assertThat(csrf.statusCode()).isEqualTo(200);
        assertThat(csrf.headers().firstValue("Set-Cookie").orElseThrow()).contains("HttpOnly", "SameSite=Strict");
        String previousId = cookies.getCookieStore().getCookies().getFirst().getValue();
        String token = mapper.readTree(csrf.body()).get("token").asText();
        assertThat(send(client, "POST", "/api/v1/auth/login", token,
                "username=fixture-admin&password=admin-fixture-only").statusCode()).isEqualTo(204);
        assertThat(cookies.getCookieStore().getCookies().getFirst().getValue()).isNotEqualTo(previousId);
        var me = send(client, "GET", "/api/v1/auth/me", null, null);
        assertThat(me.statusCode()).isEqualTo(200);
        assertThat(mapper.readTree(me.body()).get("username").asText()).isEqualTo("fixture-admin");
        assertThat(send(client, "GET", "/api/v1/overview/summary", null, null).statusCode()).isEqualTo(200);
        csrf = send(client, "GET", "/api/v1/auth/csrf", null, null);
        token = mapper.readTree(csrf.body()).get("token").asText();
        assertThat(send(client, "POST", "/api/v1/orders", token, "").statusCode()).isEqualTo(403);
        assertThat(send(client, "POST", "/api/v1/auth/logout", token, "").statusCode()).isEqualTo(204);
        assertThat(send(client, "GET", "/api/v1/auth/me", null, null).statusCode()).isEqualTo(401);
        assertThat(send(client, "GET", "/api/v1/overview/summary", null, null).statusCode()).isEqualTo(401);
    }
    private HttpResponse<String> send(HttpClient client, String method, String path, String csrf, String body) throws Exception {
        var request = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + path));
        if (csrf != null) request.header("X-CSRF-TOKEN", csrf);
        if (body != null) request.header("Content-Type", "application/x-www-form-urlencoded");
        return client.send(request.method(method, body == null ? HttpRequest.BodyPublishers.noBody()
                : HttpRequest.BodyPublishers.ofString(body)).build(), HttpResponse.BodyHandlers.ofString());
    }
}
