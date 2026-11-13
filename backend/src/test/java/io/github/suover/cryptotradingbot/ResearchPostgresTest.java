package io.github.suover.cryptotradingbot;

import static org.assertj.core.api.Assertions.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import io.github.suover.cryptotradingbot.research.dto.CandidateSearch;
import io.github.suover.cryptotradingbot.research.service.*;
import java.sql.*;
import java.time.Instant;
import java.util.List;
import javax.sql.DataSource;
import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
class ResearchPostgresTest extends io.github.suover.cryptotradingbot.support.PostgresFixtureTest {
    @Autowired ResearchReadService service;
    @Autowired DataSource dataSource;
    @Autowired MockMvc mvc;
    @Autowired jakarta.persistence.EntityManager entityManager;
    @Autowired io.github.suover.cryptotradingbot.research.repository.TradingUserRepository users;

    @Test void activeUserWithoutDataHasEmptyResults() {
        var properties = new io.github.suover.cryptotradingbot.global.config.BackendProperties(
                4, "UPBIT", "KRW", "fixture-admin", HASH);
        var repository = new io.github.suover.cryptotradingbot.research.repository.ResearchReadRepository(
                new com.querydsl.jpa.impl.JPAQueryFactory(entityManager), properties);
        var empty = new ResearchReadService(repository, users, properties, java.time.Clock.systemUTC());
        assertThat(empty.summary().candidateCount()).isZero();
        assertThat(empty.summary().latestSnapshotId()).isNull();
        assertThat(empty.summary().snapshotCount()).isZero();
        assertThat(empty.candidates(search(null, null, 0, 20)).items()).isEmpty();
        assertThat(empty.snapshots(0, 20).totalElements()).isZero();
        assertThat(empty.automationStatus().observed()).isFalse();
    }

    CandidateSearch search(String keyword, Boolean enrolled, int page, int size) {
        return new CandidateSearch(keyword, null, null, enrolled, page, size, null);
    }
    @Test void alembicSchemaAndScopeSummary() {
        var summary = service.summary();
        assertThat(summary.candidateCount()).isEqualTo(3);
        assertThat(summary.shadowEnrollmentCount()).isEqualTo(1);
        assertThat(summary.snapshotCount()).isEqualTo(3);
        assertThat(summary.latestSnapshotId()).isEqualTo(103);
        assertThat(summary.latestSnapshotCapturedAt()).isEqualTo(Instant.parse("2026-01-01T01:00:00Z"));
    }
    @Test void actualBasicAuthenticationAndJsonResponse() throws Exception {
        mvc.perform(get("/api/v1/research/summary").with(httpBasic("fixture-admin", "admin-fixture-only")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.candidateCount").value(3));
        mvc.perform(get("/api/v1/research/summary").with(httpBasic("fixture-admin", "incorrect")))
                .andExpect(status().isUnauthorized());
        mvc.perform(get("/api/v1/research/candidates/204").with(httpBasic("fixture-admin", "admin-fixture-only")))
                .andExpect(status().isNotFound());
    }
    @Test void queryDslKeywordOnlyLiteralWildcardsAndNoResults() {
        assertThat(service.candidates(search("mOmEnTuM", null, 0, 20)).items())
                .extracting("id").containsExactly(202L, 201L);
        assertThat(service.candidates(search("%", null, 0, 20)).items()).isEmpty();
        assertThat(service.candidates(search("_", null, 0, 20)).items()).isEmpty();
        assertThat(service.candidates(search("missing", null, 0, 20)).totalElements()).isZero();
    }
    @Test void registeredPeriodIncludesBothEndpoints() {
        Instant time = Instant.parse("2026-01-01T00:00:00Z");
        var result = service.candidates(new CandidateSearch(null, time, time, null, 0, 20, null));
        assertThat(result.items()).extracting("id").containsExactly(202L, 201L);
        assertThat(result.totalElements()).isEqualTo(2);
    }
    @ParameterizedTest @CsvSource({"true,1", "false,2"})
    void enrollmentExistsWithoutCountMultiplication(boolean enrolled, int expected) {
        var result = service.candidates(search(null, enrolled, 0, 20));
        assertThat(result.totalElements()).isEqualTo(expected);
        assertThat(result.items()).hasSize(expected);
        assertThat(result.items()).allMatch(item -> item.shadowEnrolled() == enrolled);
    }
    @Test void combinedConditionsAndStablePageBoundary() {
        var result = service.candidates(new CandidateSearch("momentum",
                Instant.parse("2026-01-01T00:00:00Z"), Instant.parse("2026-01-02T00:00:00Z"),
                true, 0, 20, "registeredAt,desc"));
        assertThat(result.items()).extracting("id").containsExactly(201L);
        assertThat(service.candidates(search(null, null, 0, 1)).items()).extracting("id").containsExactly(203L);
        assertThat(service.candidates(search(null, null, 1, 1)).items()).extracting("id").containsExactly(202L);
        assertThat(service.candidates(search(null, null, 2, 1)).items()).extracting("id").containsExactly(201L);
        assertThat(service.candidates(search(null, null, 3, 1)).items()).isEmpty();
        assertThat(service.candidates(search(null, null, 3, 1)).totalElements()).isEqualTo(3);
    }
    @Test void alternativeSortIsDeterministic() {
        assertThat(service.candidates(new CandidateSearch(null, null, null, null, 0, 20, "id,asc")).items())
                .extracting("id").containsExactly(201L, 202L, 203L);
        assertThat(service.candidates(new CandidateSearch(null, null, null, null, 0, 20, "policyName,desc")).items())
                .extracting("id").containsExactly(203L, 202L, 201L);
    }
    @Test void jsonbPrecisionTimeAndHistoricalGate() {
        var detail = service.candidate(201);
        assertThat(detail.componentWeights().get("liquidity"))
                .isEqualByComparingTo("0.123456789012345678901234567890");
        assertThat(detail.enrollment().storedGateStatus()).isEqualTo("ELIGIBLE_FOR_REVIEW");
        assertThat(detail.registeredAt()).isEqualTo(Instant.parse("2026-01-01T00:00:00Z"));
        assertThat(service.candidate(202).enrollment()).isNull();
    }
    @Test void numericJsonbAlsoPreservesDecimalPrecision() throws Exception {
        ownerSql("UPDATE research_policy_candidates SET component_weights = '{\"liquidity\": 0.123456789012345678901234567890}'::jsonb WHERE id=202");
        try {
            assertThat(service.candidate(202).componentWeights().get("liquidity"))
                    .isEqualByComparingTo("0.123456789012345678901234567890");
        } finally {
            ownerSql("UPDATE research_policy_candidates SET component_weights = '{\"liquidity\": \"0.123456789012345678901234567890\", \"momentum\": \"0.5\"}'::jsonb WHERE id=202");
        }
    }
    @Test void foreignUsersAndExchangesAreNotFound() {
        for (long id : List.of(204L, 205L, 206L, 999L)) {
            assertThatThrownBy(() -> service.candidate(id)).isInstanceOf(ResearchNotFoundException.class);
        }
    }
    @Test void compositeKeyReturnsEveryScopedStateAndNullCheckpoints() {
        var result = service.automationStatus();
        assertThat(result.observed()).isTrue();
        assertThat(result.states()).hasSize(2).extracting("effectiveTopN").containsExactly(7, 5);
        assertThat(result.states()).allMatch(s -> s.lastRegisteredAt() == null && s.lastStatus() == null);
    }
    @Test void snapshotsStablePaginationAndNoOtherUser() {
        assertThat(service.snapshots(0, 2).items()).extracting("id").containsExactly(103L, 102L);
        assertThat(service.snapshots(1, 2).items()).extracting("id").containsExactly(101L);
        assertThat(service.snapshots(2, 2).items()).isEmpty();
        assertThat(service.snapshots(2, 2).totalElements()).isEqualTo(3);
    }
    @Test void inactiveUserIsRejected() throws Exception {
        ownerSql("UPDATE users SET is_active=false WHERE id=1");
        try {
            assertThatThrownBy(service::summary).isInstanceOf(org.springframework.security.access.AccessDeniedException.class);
        } finally {
            ownerSql("UPDATE users SET is_active=true WHERE id=1");
        }
    }
    @Test void dbPrivilegesDenyWritesEvenWithoutReadOnlyTransaction() throws Exception {
        try (Connection connection = DriverManager.getConnection(POSTGRES.getJdbcUrl(),
                "backend_reader", "reader-fixture-only")) {
            connection.setReadOnly(false); // Prove role privileges, not merely Spring/Hikari flags.
            for (String sql : List.of(
                    "INSERT INTO users(name) VALUES ('forbidden')",
                    "UPDATE research_policy_candidates SET scenario_name='forbidden' WHERE id=201",
                    "DELETE FROM research_policy_candidates WHERE id=201",
                    "TRUNCATE research_candidate_automation_states",
                    "CREATE TABLE forbidden(id integer)",
                    "CREATE TEMP TABLE forbidden(id integer)",
                    "ALTER TABLE research_policy_candidates ADD COLUMN forbidden integer",
                    "DROP TABLE research_candidate_automation_states",
                    "SELECT * FROM order_logs")) {
                assertThatThrownBy(() -> connection.createStatement().execute(sql))
                        .isInstanceOf(SQLException.class)
                        .satisfies(error -> assertThat(((SQLException) error).getSQLState()).isEqualTo("42501"));
            }
        }
        try (Connection connection = dataSource.getConnection()) {
            assertThat(connection.isReadOnly()).isTrue();
        }
    }
    private static void ownerSql(String sql) throws Exception {
        try (Connection connection = DriverManager.getConnection(POSTGRES.getJdbcUrl(),
                POSTGRES.getUsername(), POSTGRES.getPassword());
             Statement statement = connection.createStatement()) {
            statement.execute(sql);
        }
    }
}
