package io.github.suover.cryptotradingbot;

import static org.assertj.core.api.Assertions.*;
import io.github.suover.cryptotradingbot.support.PostgresFixtureTest;
import java.net.CookieManager;
import java.net.CookiePolicy;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import tools.jackson.databind.ObjectMapper;

/** Real servlet-container cookies plus SELECT-only Alembic database, without external services. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "server.servlet.session.cookie.secure=false"
})
class AdminSessionHttpTest extends PostgresFixtureTest {
    @LocalServerPort int port;
    @Autowired ObjectMapper mapper;

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
