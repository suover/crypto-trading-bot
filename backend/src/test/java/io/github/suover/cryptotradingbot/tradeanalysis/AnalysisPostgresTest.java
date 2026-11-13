package io.github.suover.cryptotradingbot.tradeanalysis;

import static org.assertj.core.api.Assertions.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import com.querydsl.jpa.impl.JPAQueryFactory;
import io.github.suover.cryptotradingbot.global.config.BackendProperties;
import io.github.suover.cryptotradingbot.research.repository.TradingUserRepository;
import io.github.suover.cryptotradingbot.support.PostgresFixtureTest;
import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisQuery;
import io.github.suover.cryptotradingbot.tradeanalysis.repository.AnalysisReadRepository;
import io.github.suover.cryptotradingbot.tradeanalysis.service.*;
import jakarta.persistence.EntityManager;
import jakarta.persistence.EntityManagerFactory;
import org.hibernate.SessionFactory;
import java.sql.*;
import java.time.*;
import java.util.List;
import javax.sql.DataSource;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.ObjectMapper;

class AnalysisPostgresTest extends PostgresFixtureTest {
    static final long BIG = 9007199254741093L;
    static final String ROOT = "/api/v1/ai-trade-analysis/recommendations";
    @Autowired EntityManager entityManager;
    @Autowired EntityManagerFactory entityManagerFactory;
    @Autowired TradingUserRepository users;
    @Autowired StoredFeatureProjection features;
    @Autowired DataSource dataSource;
    @Autowired ObjectMapper mapper;
    @Autowired MockMvc mvc;
    AnalysisReadService scoped(long user, String exchange, String quote) {
        var p = new BackendProperties(user, exchange, quote, "fixture-admin", HASH);
        return new AnalysisReadService(new AnalysisReadRepository(new JPAQueryFactory(entityManager), p),
                users, p, features, Clock.systemUTC());
    }
    AnalysisReadService service() { return scoped(20, "UPBIT", "KRW"); }
    AnalysisQuery.Search search(int page, int size) { return new AnalysisQuery.Search(null, null, null, null, page, size); }
    @Test void listHasStablePagesExactCountsAndServerOwnership() {
        var p = service().recommendations(search(0, 2));
        assertThat(p.totalElements()).isEqualTo(14);
        assertThat(p.totalPages()).isEqualTo(7);
        assertThat(p.items()).extracting("id").containsExactly(Long.toString(BIG), "5019");
        assertThat(service().recommendations(search(1, 2)).items()).extracting("id").containsExactly("5018", "5017");
        assertThat(service().recommendations(search(7, 2)).items()).isEmpty();
        assertThat(service().recommendations(search(0, 100)).items()).extracting("id")
                .doesNotContain("5010", "5011", "5012", "5013", "5014", "5015");
    }
    @Test void inclusiveDatesExactMarketAndActionFilters() {
        var at = Instant.parse("2026-01-01T00:00:00Z");
        assertThat(service().recommendations(new AnalysisQuery.Search(at, at, "KRW-BTC", "HOLD", 0, 20)).items())
                .extracting("id").containsExactly("5003");
        assertThat(service().recommendations(new AnalysisQuery.Search(at.plusSeconds(1), null, null, null, 0, 20)).totalElements()).isZero();
        assertThat(service().recommendations(new AnalysisQuery.Search(null, null, "KRW-ETH", null, 0, 20)).totalElements()).isZero();
        assertThat(service().recommendations(new AnalysisQuery.Search(null, null, null, "SELL", 0, 20)).items())
                .extracting("id").containsExactly("5002");
        assertThat(scoped(20, "UPBIT", "BTC").recommendations(search(0, 20)).items()).extracting("id").containsExactly("5013");
        assertThat(scoped(20, "BINANCE", "KRW").recommendations(search(0, 20)).items()).isEmpty();
    }
    @ParameterizedTest @ValueSource(longs = {5010, 5011, 5012, 5013, 5014, 5015, 999999})
    void outOfScopeInvalidParentAndMissingIdsAreNotFound(long id) {
        assertThatThrownBy(() -> service().detail(id)).isInstanceOf(AnalysisNotFoundException.class);
        assertThatThrownBy(() -> service().candidates(id, new AnalysisQuery.Pagination(0, 20)))
                .isInstanceOf(AnalysisNotFoundException.class);
    }
    @Test void selectedCandidateAnchorsExactUniverseRunNotLatestRunOrRecommendationRun() {
        var result = service().detail(5001);
        assertThat(result.analysis().id()).isEqualTo("6001");
        assertThat(result.analysis().pipelineRunId()).isEqualTo("pipeline-5001");
        assertThat(result.universe().analysisRunId()).isEqualTo("7001");
        assertThat(result.universe().status()).isEqualTo("VERIFIED");
        assertThat(result.selectedCandidate().id()).isEqualTo("8001");
        var first = service().candidates(5001, new AnalysisQuery.Pagination(0, 1));
        assertThat(first.totalElements()).isEqualTo(2);
        assertThat(first.items()).extracting("id").containsExactly("8001");
        var second = service().candidates(5001, new AnalysisQuery.Pagination(1, 1));
        assertThat(second.items()).extracting("id").containsExactly("8002");
        assertThat(second.items().getFirst().selectionSource()).isEqualTo("HELD");
        assertThat(second.items().getFirst().buyEligible()).isFalse();
        assertThat(second.items().getFirst().sellEligible()).isTrue();
        assertThat(service().candidates(5001, new AnalysisQuery.Pagination(0, 100)).items())
                .extracting("id").doesNotContain("8201", "8003", "8004", "8005");
    }
    @ParameterizedTest @ValueSource(longs = {5005, 5006, 5007, 5008, 5009, 5016, 5017, 5018, 5019})
    void invalidCandidateProvenanceNeverFallsBackOrExposesForeignIds(long id) {
        var d = service().detail(id);
        assertThat(d.universe().status()).isEqualTo("INVALID_REFERENCE");
        assertThat(d.universe().selectedCandidateId()).isNull();
        assertThat(d.universe().analysisRunId()).isNull();
        assertThat(d.selectedCandidate()).isNull();
        var p = service().candidates(id, new AnalysisQuery.Pagination(0, 20));
        assertThat(p.universe().status()).isEqualTo("INVALID_REFERENCE");
        assertThat(p.items()).isEmpty();
    }
    @Test void holdAndLegacyAbsenceAreNotFailureOrInventedApproval() {
        var hold = service().detail(5003);
        assertThat(hold.recommendation().action()).isEqualTo("HOLD");
        assertThat(hold.recommendation().tradeRatio()).isNull();
        assertThat(hold.approvals()).isEmpty();
        assertThat(hold.executionAttempts()).isEmpty();
        assertThat(hold.order()).isNull();
        assertThat(hold.universe().status()).isEqualTo("NO_REFERENCE");
        var legacy = service().detail(5004);
        assertThat(legacy.analysis().pipelineRunId()).isNull();
        assertThat(legacy.universe().status()).isEqualTo("NO_REFERENCE");
        assertThat(legacy.order().tradingMode()).isEqualTo("MOCK");
        assertThat(legacy.order().executedQuantity()).isNull();
        assertThat(legacy.approvals().getFirst().status()).isEqualTo("EXPIRED");
    }
    @Test void approvalAttemptAndExecutionRemainSeparateWithExactStoredValues() {
        var d = service().detail(5001);
        assertThat(d.recommendation().status()).isEqualTo("APPROVED");
        assertThat(d.finalDecisionReason()).isEqualTo("Final decision after application safety checks");
        assertThat(d.recommendation().tradeRatio()).isEqualTo("0.123456789");
        assertThat(d.recommendedQuantity()).isEqualTo("0.0000000001");
        assertThat(d.approvals()).extracting("id").containsExactly("9501");
        assertThat(d.executionAttempts()).extracting("id").containsExactly("9601", "9603");
        assertThat(d.executionAttempts().getLast().orderLogId()).isNull();
        assertThat(d.order().status()).isEqualTo("LIVE_EXECUTED_CANCELLED");
        assertThat(d.order().amountKrw()).isEqualTo("10000.12");
        assertThat(d.order().quantity()).isEqualTo("2.0000000000");
        assertThat(d.order().price()).isEqualTo("5000.0000000000");
        assertThat(d.order().executedQuantity()).isEqualTo("1.0000000000");
        assertThat(d.order().executedFundsKrw()).isEqualTo("1500.0000000000");
        assertThat(d.order().averageExecutionPrice()).isEqualTo("1500.0000000000");
        assertThat(d.order().remainingQuantity()).isEqualTo("1.0000000000");
        assertThat(d.order().paidFee()).isEqualTo("0.0000000000");
    }
    @Test void unknownOrderDoesNotBecomeExecutedOrZeroAndWrongOrderJoinsAreHidden() {
        var d = service().detail(5002);
        assertThat(d.order().status()).isEqualTo("LIVE_UNKNOWN");
        assertThat(d.order().executedFundsKrw()).isNull();
        assertThat(d.order().executionSyncedAt()).isNull();
        assertThat(d.executionAttempts().getFirst().status()).isEqualTo("UNKNOWN");
        for (long id : List.of(5005L, 5006L, 5007L, 5008L)) assertThat(service().detail(id).order()).isNull();
    }
    @Test void featureAllowlistAndDecimalsSurviveActualJsonbMapping() {
        var c = service().detail(5001).selectedCandidate();
        assertThat(c.score()).isEqualTo("0.123456789");
        assertThat(c.quoteTradeValue24h()).isEqualTo("1234567890123456789012345678.12");
        assertThat(c.features().dataQuality()).isEqualTo("PARTIAL");
        assertThat(c.features().projectionStatus()).isEqualTo("COMPLETE");
        assertThat(c.features().timeframes()).extracting("timeframe").containsExactly("15m", "1h", "4h", "1d");
        assertThat(c.features().timeframes().getFirst().indicators().get("rsi_14"))
                .isEqualTo("71.1234567890123456789012345678");
        String wire = mapper.writeValueAsString(service().detail(5001));
        assertThat(wire).doesNotContain("fixture-private", "callback", "telegram", "rawResponse", "aiResponse",
                "exchangeOrderId", "errorMessage", "private_extra");
        assertThat(wire).contains("\"recommendedQuantity\":\"0.0000000001\"");
    }
    @Test void inactiveOrMissingUsersFailClosedBeforeQueries() {
        for (long user : List.of(3L, 999L)) {
            var service = scoped(user, "UPBIT", "KRW");
            assertThatThrownBy(() -> service.recommendations(search(0, 20))).isInstanceOf(AccessDeniedException.class);
            assertThatThrownBy(() -> service.detail(5001)).isInstanceOf(AccessDeniedException.class);
            assertThatThrownBy(() -> service.candidates(5001, new AnalysisQuery.Pagination(0, 20)))
                    .isInstanceOf(AccessDeniedException.class);
        }
    }
    @Test void listAndCandidatePagesDoNotIssueOneQueryPerRow() {
        var stats = entityManagerFactory.unwrap(SessionFactory.class).getStatistics();
        stats.setStatisticsEnabled(true);
        try {
            stats.clear();
            assertThat(service().recommendations(search(0, 100)).items()).hasSize(14);
            assertThat(stats.getPrepareStatementCount()).isLessThanOrEqualTo(3);
            stats.clear();
            assertThat(service().candidates(5001, new AnalysisQuery.Pagination(0, 100)).items()).hasSize(2);
            assertThat(stats.getPrepareStatementCount()).isLessThanOrEqualTo(6);
        } finally { stats.setStatisticsEnabled(false); }
    }
    @Test void realHttpBasicAndStringSafeContractForExistingUserRemainCompatible() throws Exception {
        mvc.perform(get(ROOT + "/4001")).andExpect(status().isUnauthorized());
        mvc.perform(get(ROOT + "/4001").with(httpBasic("fixture-admin", "wrong"))).andExpect(status().isUnauthorized());
        mvc.perform(get(ROOT + "/4001").with(user("viewer").roles("VIEWER"))).andExpect(status().isForbidden());
        mvc.perform(get(ROOT + "/4001").with(httpBasic("fixture-admin", "admin-fixture-only")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.recommendation.id").value("4001"))
                .andExpect(jsonPath("$.recommendation.tradeRatio").value("0.123456789"))
                .andExpect(jsonPath("$.universe.status").value("NO_REFERENCE"))
                .andExpect(jsonPath("$.order").isEmpty())
                .andExpect(jsonPath("$.recommendation.aiResponse").doesNotExist());
        mvc.perform(get(ROOT + "/5001").with(httpBasic("fixture-admin", "admin-fixture-only")))
                .andExpect(status().isNotFound());
        mvc.perform(get(ROOT + "/4001/candidates").with(httpBasic("fixture-admin", "admin-fixture-only")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items").isEmpty());
    }
    @Test void newColumnGrantsDenyWritesAndPrivateColumnsEvenWithoutReadOnlyFlag() throws Exception {
        try (Connection c = DriverManager.getConnection(POSTGRES.getJdbcUrl(), "backend_reader", "reader-fixture-only")) {
            c.setReadOnly(false);
            for (String table : List.of("analysis_runs", "market_universe_candidates", "approval_requests",
                    "order_execution_attempts", "order_logs")) {
                c.createStatement().execute("SELECT id FROM " + table + " LIMIT 1");
                for (String sql : List.of("INSERT INTO " + table + " DEFAULT VALUES",
                        "UPDATE " + table + " SET id=id WHERE false", "DELETE FROM " + table + " WHERE false",
                        "TRUNCATE " + table, "ALTER TABLE " + table + " ADD COLUMN forbidden integer",
                        "DROP TABLE " + table)) denied(c, sql);
            }
            for (String sql : List.of("SELECT callback_token FROM approval_requests",
                    "SELECT telegram_chat_id FROM approval_requests", "SELECT error_message FROM analysis_runs",
                    "SELECT raw_response FROM order_logs", "SELECT exchange_order_id FROM order_logs",
                    "SELECT error_message FROM order_execution_attempts", "SELECT * FROM order_fills",
                    "SELECT * FROM account_snapshots", "CREATE TABLE forbidden(id integer)",
                    "CREATE TEMP TABLE forbidden(id integer)")) denied(c, sql);
        }
        try (Connection c = dataSource.getConnection()) { assertThat(c.isReadOnly()).isTrue(); }
    }
    @Test void tooManyApprovalsFailInsteadOfSilentlyTruncatingHistory() throws Exception {
        // Owner access is restricted to this disposable Testcontainers database.
        try (Connection c = DriverManager.getConnection(POSTGRES.getJdbcUrl(), POSTGRES.getUsername(), POSTGRES.getPassword());
             var s = c.createStatement()) {
            s.execute("""
                    INSERT INTO approval_requests
                        (id, recommendation_id, user_id, status, callback_token, expires_at)
                    SELECT i, 5003, 20, 'EXPIRED', 'fixture-bound-' || i, now()
                    FROM generate_series(100000, 101000) i
                    """);
            try {
                assertThatThrownBy(() -> service().detail(5003)).isInstanceOf(IllegalStateException.class)
                        .hasMessage("Related read bound exceeded");
            } finally { s.execute("DELETE FROM approval_requests WHERE id BETWEEN 100000 AND 101000"); }
        }
    }
    private static void denied(Connection c, String sql) {
        assertThatThrownBy(() -> { try (var s = c.createStatement()) { s.execute(sql); } })
                .isInstanceOf(SQLException.class).satisfies(e ->
                        assertThat(((SQLException) e).getSQLState()).isEqualTo("42501"));
    }
}
