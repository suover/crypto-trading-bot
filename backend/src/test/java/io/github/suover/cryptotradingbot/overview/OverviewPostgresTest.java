package io.github.suover.cryptotradingbot.overview;

import static org.assertj.core.api.Assertions.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import com.querydsl.jpa.impl.JPAQueryFactory;
import io.github.suover.cryptotradingbot.global.config.BackendProperties;
import io.github.suover.cryptotradingbot.overview.dto.OverviewQuery;
import io.github.suover.cryptotradingbot.overview.repository.OverviewReadRepository;
import io.github.suover.cryptotradingbot.overview.service.OverviewReadService;
import io.github.suover.cryptotradingbot.research.repository.TradingUserRepository;
import io.github.suover.cryptotradingbot.support.PostgresFixtureTest;
import jakarta.persistence.EntityManager;
import java.sql.*;
import java.time.*;
import java.util.List;
import javax.sql.DataSource;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.web.servlet.MockMvc;

class OverviewPostgresTest extends PostgresFixtureTest {
    static final long BIG = 9007199254740993L;
    @Autowired OverviewReadService service;
    @Autowired OverviewReadRepository repository;
    @Autowired EntityManager entityManager;
    @Autowired TradingUserRepository users;
    @Autowired DataSource dataSource;
    @Autowired MockMvc mvc;
    OverviewReadService scoped(long user, String exchange, String quote) {
        var props = new BackendProperties(user, exchange, quote, "fixture-admin", HASH);
        return new OverviewReadService(new OverviewReadRepository(new JPAQueryFactory(entityManager), props),
                users, props, Clock.systemUTC());
    }
    OverviewQuery.Performance history(int page, int size) {
        return new OverviewQuery.Performance(null, null, page, size);
    }
    @Test void latestUsesCapturedTimeThenIdNotMaximumId() {
        var result = service.summary();
        assertThat(result.portfolio().snapshotId()).isEqualTo(Long.toString(BIG));
        assertThat(result.portfolio().capturedAt()).isEqualTo(Instant.parse("2026-01-04T00:00:00Z"));
        assertThat(result.performance().portfolioSnapshotId()).isEqualTo(result.portfolio().snapshotId());
        assertThat(result.performance().performanceStatus()).isEqualTo("PARTIAL");
        assertThat(result.performance().calculatedAt()).isEqualTo(Instant.parse("2026-01-04T01:00:00Z"));
        assertThat(result.botPnl().calculatedAt()).isEqualTo(Instant.parse("2026-01-01T02:00:00Z"));
    }
    @Test void partialTotalStaysNullDespiteKnownValueAndFifoValue() {
        var s = service.summary();
        assertThat(s.portfolio().totalValueKrw()).isNull();
        assertThat(s.portfolio().knownTotalValueKrw()).isEqualTo("12345678901234567890.1234567890");
        assertThat(s.portfolio().unpricedAssetCount()).isEqualTo(1);
        assertThat(s.portfolio().missingCostBasisCount()).isEqualTo(1);
        assertThat(s.portfolio().unrealizedPnlKrw()).isNull();
        assertThat(s.performance().performanceIndex()).isNull();
        assertThat(s.botPnl().accountingStatus()).isEqualTo("PARTIAL");
        assertThat(s.botPnl().recognizedRealizedPnlKrw()).isEqualTo("-12345.6789000000");
        assertThat(s.botPnl().scope().userId()).isEqualTo("1");
        assertThat(s.botPnl().scope().amountCurrency()).isEqualTo("KRW");
    }
    @Test void completeStoredValuesAndPerformanceAreNotRecomputed() {
        var s = scoped(10, "UPBIT", "KRW").summary();
        assertThat(s.portfolio().valuationStatus()).isEqualTo("COMPLETE");
        assertThat(s.portfolio().totalValueKrw()).isEqualTo("14000.0000000000");
        assertThat(s.portfolio().unrealizedPnlKrw()).isEqualTo("0.0000000000");
        assertThat(s.performance().periodReturnPercentage()).isEqualTo("1.1234567890");
        assertThat(s.performance().cumulativeReturnPercentage()).isEqualTo("2.1234567890");
        assertThat(s.performance().performanceIndex()).isEqualTo("102.1234567890");
        assertThat(s.performance().drawdownIndex()).isEqualTo("-3.0000000000");
        assertThat(s.performance().drawdownPercentage()).isEqualTo("-2.1234567890");
        assertThat(s.botPnl().accountingStatus()).isEqualTo("COMPLETE");
    }
    @Test void oldPerformanceMustNotStandInForMissingCurrentPerformance() {
        var s = scoped(11, "UPBIT", "KRW");
        assertThat(s.summary().portfolio().snapshotId()).isEqualTo("1102");
        assertThat(s.summary().performance()).isNull();
        assertThat(s.summary().botPnl()).isNull();
        assertThat(s.performance(history(0, 90)).items()).extracting("portfolioSnapshotId").containsExactly("1101");
        assertThat(s.performance(history(0, 90)).items().getFirst().performanceStatus()).isEqualTo("BASELINE");
    }
    @Test void noPortfolioVersusPortfolioWithNoPositions() {
        var empty = scoped(4, "UPBIT", "KRW");
        assertThat(empty.summary().portfolio()).isNull();
        assertThat(empty.summary().performance()).isNull();
        assertThat(empty.summary().botPnl()).isNull();
        assertThat(empty.positions().portfolio()).isNull();
        assertThat(empty.positions().items()).isEmpty();
        var present = scoped(10, "UPBIT", "KRW").positions();
        assertThat(present.portfolio()).isNotNull();
        assertThat(present.items()).isEmpty();
    }
    @Test void positionsUseParentOwnershipAndDoNotLoseUnpricedAssets() {
        var p = service.positions();
        assertThat(p.portfolio().snapshotId()).isEqualTo(Long.toString(BIG));
        assertThat(p.items()).extracting("currency").containsExactly("BTC", "ETH", "APENFT");
        assertThat(p.items()).allMatch(item -> item.portfolioSnapshotId().equals(Long.toString(BIG)));
        assertThat(p.items().get(0).availableQuantity()).isEqualTo("0.1234567890");
        assertThat(p.items().get(0).lockedQuantity()).isEqualTo("0.1000000000");
        assertThat(p.items().get(1).estimatedCostBasisKrw()).isNull();
        assertThat(p.items().get(2).valuationStatus()).isEqualTo("UNPRICED");
        assertThat(p.items().get(2).market()).isNull();
        assertThat(p.items().get(2).marketValueKrw()).isNull();
        assertThat(repository.positions(2001)).isEmpty();
        assertThat(repository.positions(2002)).isEmpty();
        assertThat(repository.positions(2003)).isEmpty();
        assertThat(repository.performance(1004)).isEmpty();
        assertThat(repository.performance(1005)).isEmpty();
        assertThat(repository.performance(2004)).isEmpty();
    }
    @Test void historyParentQuoteAndBothOwnerFieldsAreScopedWithStablePages() {
        assertThat(service.performance(history(0, 2)).items()).extracting("id")
                .containsExactly(Long.toString(BIG), "1002");
        var p = service.performance(history(1, 2));
        assertThat(p.items()).extracting("id").containsExactly(Long.toString(BIG + 1), "1001");
        assertThat(p.totalElements()).isEqualTo(4);
        assertThat(p.totalPages()).isEqualTo(2);
        assertThat(service.performance(history(2, 2)).items()).isEmpty();
        assertThat(service.performance(history(2, 2)).totalElements()).isEqualTo(4);
        assertThat(p.items().getLast().performanceStatus()).isEqualTo("BASELINE");
        assertThat(p.items().getLast().cumulativeReturnPercentage()).isEqualTo("0.0000000000");
        assertThat(p.items().getLast().periodReturnPercentage()).isNull();
        assertThat(p.items().getLast().periodStartAt()).isNull();
    }
    @Test void periodEndRangeIncludesBothEndpointsAndEmptyIsAnEmptyPage() {
        Instant at = Instant.parse("2026-01-04T00:00:00Z");
        var exact = service.performance(new OverviewQuery.Performance(at, at, 0, 90));
        assertThat(exact.totalElements()).isEqualTo(2);
        assertThat(exact.items()).extracting("id").containsExactly(Long.toString(BIG), "1002");
        assertThat(service.performance(new OverviewQuery.Performance(at.plusSeconds(1), null, 0, 90)).totalElements()).isZero();
    }
    @ParameterizedTest @CsvSource({"2,UPBIT,KRW,2001", "1,BINANCE,KRW,2002", "1,UPBIT,BTC,2003"})
    void alternativeScopeDoesNotMixOwnersExchangesOrQuotes(long user, String exchange, String quote, String latest) {
        var s = scoped(user, exchange, quote);
        assertThat(s.summary().portfolio().snapshotId()).isEqualTo(latest);
        assertThat(s.summary().performance().portfolioSnapshotId()).isEqualTo(latest);
        if (quote.equals("BTC")) assertThat(s.summary().botPnl()).isNull();
        else {
            assertThat(s.summary().botPnl().scope().userId()).isEqualTo(Long.toString(user));
            assertThat(s.summary().botPnl().scope().exchange()).isEqualTo(exchange);
        }
    }
    @Test void recommendationPrefixSortPrecisionAndCount() {
        var p = service.recommendations(new OverviewQuery.Recommendations(null, 0, 2));
        assertThat(p.totalElements()).isEqualTo(4);
        assertThat(p.totalPages()).isEqualTo(2);
        assertThat(p.items()).extracting("id").containsExactly("4003", "4002");
        assertThat(p.items().getFirst().action()).isEqualTo("HOLD");
        assertThat(p.items().getFirst().tradeRatio()).isNull();
        assertThat(p.items().get(1).confidence()).isEqualTo("0.9876");
        assertThat(p.items().get(1).tradeRatio()).isEqualTo("0.123456789");
        assertThat(p.items().get(1).status()).isEqualTo("APPROVED");
        var next = service.recommendations(new OverviewQuery.Recommendations(null, 1, 2));
        assertThat(next.items()).extracting("id").containsExactly("4001", Long.toString(BIG + 10));
        assertThat(next.items().getLast().analysisRunId()).isEqualTo(Long.toString(BIG + 10));
        assertThat(scoped(1, "UPBIT", "BTC").recommendations(new OverviewQuery.Recommendations(null, 0, 10)).items())
                .extracting("market").containsExactly("BTC-ETH");
        assertThat(scoped(1, "BINANCE", "KRW").recommendations(new OverviewQuery.Recommendations(null, 0, 10)).items()).isEmpty();
    }
    @ParameterizedTest @CsvSource({"BUY,2", "SELL,1", "HOLD,1"})
    void actionFilterCounts(String action, int count) {
        var result = service.recommendations(new OverviewQuery.Recommendations(action, 0, 10));
        assertThat(result.totalElements()).isEqualTo(count);
        assertThat(result.items()).allMatch(r -> r.action().equals(action));
    }
    @ParameterizedTest @ValueSource(strings = {"summary", "positions", "performance", "recommendations"})
    void actualSecurityAndSafeWireContract(String endpoint) throws Exception {
        String url = "/api/v1/overview/" + endpoint;
        mvc.perform(get(url)).andExpect(status().isUnauthorized());
        mvc.perform(get(url).with(httpBasic("fixture-admin", "wrong"))).andExpect(status().isUnauthorized());
        mvc.perform(get(url).with(user("viewer").roles("VIEWER"))).andExpect(status().isForbidden());
        String body = mvc.perform(get(url).session(adminSession(mvc)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.scope.userId").isString())
                .andReturn().getResponse().getContentAsString();
        assertThat(body).doesNotContain("aiResponse", "private-reason", "not-exposed", "sourceSignature", "telegram", "password");
    }
    @Test void inactiveConfiguredUserProduces403() throws Exception {
        ownerSql("UPDATE users SET is_active=false WHERE id=1");
        try {
            for (String endpoint : List.of("summary", "positions", "performance", "recommendations")) {
                mvc.perform(get("/api/v1/overview/" + endpoint).session(adminSession(mvc)))
                        .andExpect(status().isForbidden());
            }
        } finally { ownerSql("UPDATE users SET is_active=true WHERE id=1"); }
        assertThatThrownBy(() -> scoped(3, "UPBIT", "KRW").summary()).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> scoped(999, "UPBIT", "KRW").summary()).isInstanceOf(AccessDeniedException.class);
    }
    @Test void readerPrivilegesDenyEveryWriteWithoutSpringReadOnlyFlag() throws Exception {
        try (Connection c = DriverManager.getConnection(POSTGRES.getJdbcUrl(), "backend_reader", "reader-fixture-only")) {
            c.setReadOnly(false);
            for (String table : List.of("portfolio_snapshots", "portfolio_position_snapshots",
                    "portfolio_performance_snapshots", "bot_trading_pnl_summaries", "trade_recommendations")) {
                c.createStatement().execute("SELECT id FROM " + table + " LIMIT 1");
                for (String sql : List.of("INSERT INTO " + table + " DEFAULT VALUES",
                        "UPDATE " + table + " SET id=id WHERE false", "DELETE FROM " + table + " WHERE false",
                        "TRUNCATE " + table, "ALTER TABLE " + table + " ADD COLUMN forbidden integer",
                        "DROP TABLE " + table)) denied(c, sql);
            }
            for (String sql : List.of("CREATE TABLE forbidden(id integer)", "CREATE TEMP TABLE forbidden(id integer)",
                    "SELECT * FROM order_logs", "SELECT * FROM account_snapshots", "SELECT * FROM analysis_runs")) denied(c, sql);
        }
        try (Connection c = dataSource.getConnection()) { assertThat(c.isReadOnly()).isTrue(); }
    }
    private static void denied(Connection c, String sql) {
        assertThatThrownBy(() -> { try (var statement = c.createStatement()) { statement.execute(sql); } })
                .isInstanceOf(SQLException.class)
                .satisfies(e -> assertThat(((SQLException) e).getSQLState()).isEqualTo("42501"));
    }
    private static void ownerSql(String sql) throws Exception {
        try (Connection c = DriverManager.getConnection(POSTGRES.getJdbcUrl(), POSTGRES.getUsername(), POSTGRES.getPassword());
             var statement = c.createStatement()) { statement.execute(sql); }
    }
}
