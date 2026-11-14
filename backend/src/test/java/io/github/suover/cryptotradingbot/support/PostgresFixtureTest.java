package io.github.suover.cryptotradingbot.support;

import java.nio.file.*;
import java.sql.DriverManager;
import java.util.List;
import java.util.concurrent.TimeUnit;
import io.github.suover.cryptotradingbot.global.security.CurrentTradingScope;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.AfterAll;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.postgresql.PostgreSQLContainer;

@SpringBootTest
@AutoConfigureMockMvc
@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_CLASS)
public abstract class PostgresFixtureTest {
    protected static org.springframework.mock.web.MockHttpSession adminSession(
            org.springframework.test.web.servlet.MockMvc mvc) throws Exception {
        return adminSession(mvc, "fixture-admin");
    }
    protected static org.springframework.mock.web.MockHttpSession adminSession(
            org.springframework.test.web.servlet.MockMvc mvc, String username) throws Exception {
        return (org.springframework.mock.web.MockHttpSession) mvc.perform(
                org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post("/api/v1/auth/login")
                .with(org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf())
                .param("username", username).param("password", "admin-fixture-only"))
                .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.status().isNoContent())
                .andReturn().getRequest().getSession();
    }
    protected static final PostgreSQLContainer POSTGRES = new PostgreSQLContainer("postgres:17")
            .withDatabaseName("backend_fixture").withUsername("fixture_owner").withPassword("fixture-only");
    protected static final String HASH = new BCryptPasswordEncoder().encode("admin-fixture-only");

    protected static CurrentTradingScope fixtureScope(EntityManager entityManager, long user, String exchange, String quote) {
        return new CurrentTradingScope(null, null, null) {
            @Override public Scope resolve() {
                var linked = entityManager.find(io.github.suover.cryptotradingbot.research.entity.TradingUser.class, user);
                if (linked == null || !linked.getIsActive()) throw new AccessDeniedException("Fixture user unavailable");
                return new Scope(user, exchange, quote);
            }
        };
    }

    private static void prepareAuth(Path root) throws Exception {
        try (var c = DriverManager.getConnection(POSTGRES.getJdbcUrl(), POSTGRES.getUsername(), POSTGRES.getPassword());
             var sql = c.createStatement()) {
            sql.execute("CREATE ROLE dashboard_auth_writer LOGIN PASSWORD 'writer-fixture-only'");
            String permissions = Files.readString(root.resolve("backend/scripts/dashboard_auth_permissions.sql"));
            sql.execute(permissions.substring(permissions.indexOf("BEGIN;"))
                    .replace(":\"DBNAME\"", "\"backend_fixture\""));
            try (var insert = c.prepareStatement(
                    "INSERT INTO auth.accounts(username, password_hash, trading_user_id) VALUES (?, ?, ?)")) {
                insert.setString(2, HASH);
                insert.setString(1, "fixture-admin"); insert.setLong(3, 1); insert.executeUpdate();
                insert.setString(1, "fixture-other"); insert.setLong(3, 20); insert.executeUpdate();
            }
        }
    }

    @DynamicPropertySource
    protected static void database(DynamicPropertyRegistry registry) throws Exception {
        // Docker absence or Alembic failure fails the suite; never silently skip or use an external DB.
        POSTGRES.start();
        try {
            Path root = Path.of(System.getProperty("repository.root"));
            Path log = root.resolve("backend/build/alembic-test.log");
            Files.createDirectories(log.getParent());
            String runner = System.getProperty("test.migration.runner", "uv");
            if (!List.of("uv", "docker").contains(runner)) {
                throw new IllegalArgumentException("Unsupported test migration runner");
            }
            boolean docker = runner.equals("docker");
            String url = "postgresql+psycopg://fixture_owner:fixture-only@"
                    + (docker ? "host.docker.internal" : POSTGRES.getHost()) + ":"
                    + POSTGRES.getMappedPort(5432) + "/backend_fixture";
            ProcessBuilder builder = docker
                    ? new ProcessBuilder("docker", "run", "--rm",
                        "--add-host=host.docker.internal:host-gateway",
                        "--mount", "type=bind,source=" + root.resolve("trading-engine")
                                + ",target=/workspace/trading-engine,readonly",
                        "--mount", "type=bind,source=" + root.resolve("backend/src/test/python")
                                + ",target=/workspace/backend/src/test/python,readonly",
                        "-e", "BACKEND_TEST_MIGRATION_URL=" + url,
                        "-e", "UV_PROJECT_ENVIRONMENT=/tmp/fixture-venv",
                        "-e", "PYTHONPATH=/workspace/trading-engine",
                        "-e", "PYTHONDONTWRITEBYTECODE=1",
                        "ghcr.io/astral-sh/uv:python3.14-bookworm-slim",
                        "uv", "run", "--locked", "--project", "/workspace/trading-engine",
                        "python", "/workspace/backend/src/test/python/prepare_database.py")
                    : new ProcessBuilder("uv", "run", "--locked", "--project",
                        root.resolve("trading-engine").toString(), "python",
                        root.resolve("backend/src/test/python/prepare_database.py").toString());
            builder.directory(root.resolve("backend").toFile());
            builder.environment().put("PYTHONPATH", root.resolve("trading-engine").toString());
            builder.environment().put("BACKEND_TEST_MIGRATION_URL", url);
            builder.redirectErrorStream(true).redirectOutput(log.toFile());
            Process process = builder.start();
            if (!process.waitFor(180, TimeUnit.SECONDS)) {
                process.destroyForcibly();
                throw new IllegalStateException("Alembic fixture preparation timed out");
            }
            if (process.exitValue() != 0) {
                throw new IllegalStateException("Alembic fixture preparation failed; see build/alembic-test.log");
            }
            prepareAuth(root);
        } catch (Exception failure) {
            POSTGRES.stop();
            throw failure;
        }
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", () -> "backend_reader");
        registry.add("spring.datasource.password", () -> "reader-fixture-only");
        registry.add("backend.exchange", () -> "UPBIT");
        registry.add("backend.quote-asset", () -> "KRW");
    }
    @AfterAll protected static void stopDatabase() { POSTGRES.stop(); }
}
