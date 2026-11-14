package io.github.suover.cryptotradingbot.support;

import java.nio.file.*;
import java.util.List;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterAll;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
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
        return (org.springframework.mock.web.MockHttpSession) mvc.perform(
                org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post("/api/v1/auth/login")
                .with(org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf())
                .param("username", "fixture-admin").param("password", "admin-fixture-only"))
                .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.status().isNoContent())
                .andReturn().getRequest().getSession();
    }
    protected static final PostgreSQLContainer POSTGRES = new PostgreSQLContainer("postgres:17")
            .withDatabaseName("backend_fixture").withUsername("fixture_owner").withPassword("fixture-only");
    protected static final String HASH = new BCryptPasswordEncoder().encode("admin-fixture-only");

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
        } catch (Exception failure) {
            POSTGRES.stop();
            throw failure;
        }
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", () -> "backend_reader");
        registry.add("spring.datasource.password", () -> "reader-fixture-only");
        registry.add("backend.trading-user-id", () -> 1);
        registry.add("backend.exchange", () -> "UPBIT");
        registry.add("backend.quote-asset", () -> "KRW");
        registry.add("backend.admin-username", () -> "fixture-admin");
        registry.add("backend.admin-password-hash", () -> HASH);
    }
    @AfterAll protected static void stopDatabase() { POSTGRES.stop(); }
}
