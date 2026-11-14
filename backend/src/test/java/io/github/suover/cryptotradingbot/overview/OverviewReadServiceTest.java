package io.github.suover.cryptotradingbot.overview;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import io.github.suover.cryptotradingbot.global.security.CurrentTradingScope;
import io.github.suover.cryptotradingbot.overview.dto.OverviewQuery;
import io.github.suover.cryptotradingbot.overview.entity.*;
import io.github.suover.cryptotradingbot.overview.repository.OverviewReadRepository;
import io.github.suover.cryptotradingbot.overview.service.OverviewReadService;
import java.math.BigDecimal;
import java.time.*;
import java.util.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.transaction.annotation.*;

class OverviewReadServiceTest {
    static final Instant NOW = Instant.parse("2026-02-01T00:00:00Z");
    static final long BIG = 9007199254740993L;
    final OverviewReadRepository repository = mock(OverviewReadRepository.class);
    final CurrentTradingScope tradingScope = mock(CurrentTradingScope.class);
    static final CurrentTradingScope.Scope SCOPE = new CurrentTradingScope.Scope(1, "UPBIT", "KRW");
    final OverviewReadService service = new OverviewReadService(repository, tradingScope, Clock.fixed(NOW, ZoneOffset.UTC));
    @BeforeEach void active() {
        when(tradingScope.resolve()).thenReturn(SCOPE);
    }
    PortfolioSnapshot portfolio(String status) {
        var s = mock(PortfolioSnapshot.class);
        when(s.getId()).thenReturn(BIG);
        when(s.getCapturedAt()).thenReturn(NOW.minusSeconds(3600));
        when(s.getValuationStatus()).thenReturn(status);
        when(s.getKnownTotalValueKrw()).thenReturn(new BigDecimal("12345678901234567890.1234567890"));
        when(repository.latestPortfolio(SCOPE)).thenReturn(Optional.of(s));
        return s;
    }
    @Test void noSnapshotIsNotAZeroAccountAndPnlRemainsIndependent() {
        var result = service.summary();
        assertThat(result.portfolio()).isNull();
        assertThat(result.performance()).isNull();
        assertThat(result.botPnl()).isNull();
        assertThat(result.generatedAt()).isEqualTo(NOW);
        assertThat(result.scope().userId()).isEqualTo("1");
        verify(repository, never()).performance(any(), anyLong());
        var positions = service.positions();
        assertThat(positions.portfolio()).isNull();
        assertThat(positions.items()).isEmpty();
        verify(repository, never()).positions(any(), anyLong());
    }
    @Test void partialDoesNotUseKnownTotalAsCompleteTotal() {
        portfolio("PARTIAL");
        var result = service.summary();
        assertThat(result.portfolio().snapshotId()).isEqualTo("9007199254740993");
        assertThat(result.portfolio().totalValueKrw()).isNull();
        assertThat(result.portfolio().knownTotalValueKrw()).isEqualTo("12345678901234567890.1234567890");
        assertThat(result.portfolio().valuationStatus()).isEqualTo("PARTIAL");
        assertThat(result.performance()).isNull();
        verify(repository).performance(SCOPE, BIG);
    }
    @Test void completeZeroAndMissingPerformanceRemainDistinct() {
        var s = portfolio("COMPLETE");
        when(s.getTotalValueKrw()).thenReturn(new BigDecimal("0.0000000000"));
        var result = service.summary();
        assertThat(result.portfolio().totalValueKrw()).isEqualTo("0.0000000000");
        assertThat(result.performance()).isNull();
        assertThat(result.portfolio().capturedAt()).isNotEqualTo(result.generatedAt());
    }
    @ParameterizedTest @ValueSource(strings = {"BASELINE", "COMPLETE", "PARTIAL"})
    void performanceStateAndStoredUnitsAreUnchanged(String status) {
        portfolio("COMPLETE");
        var p = mock(PortfolioPerformanceSnapshot.class);
        when(p.getPortfolioSnapshotId()).thenReturn(BIG);
        when(p.getPerformanceStatus()).thenReturn(status);
        when(p.getReturnMethod()).thenReturn("MODIFIED_DIETZ");
        when(p.getCumulativeReturnPercentage()).thenReturn(status.equals("PARTIAL") ? null :
                new BigDecimal(status.equals("BASELINE") ? "0" : "1.1234567890"));
        when(p.getCalculatedAt()).thenReturn(NOW.minusSeconds(60));
        when(repository.performance(SCOPE, BIG)).thenReturn(Optional.of(p));
        var result = service.summary().performance();
        assertThat(result.portfolioSnapshotId()).isEqualTo(Long.toString(BIG));
        assertThat(result.performanceStatus()).isEqualTo(status);
        assertThat(result.cumulativeReturnPercentage()).isEqualTo(status.equals("PARTIAL") ? null :
                status.equals("BASELINE") ? "0" : "1.1234567890");
        assertThat(result.calculatedAt()).isEqualTo(NOW.minusSeconds(60));
        assertThat(result.returnMethod()).isEqualTo("MODIFIED_DIETZ");
    }
    @ParameterizedTest @ValueSource(strings = {"COMPLETE", "PARTIAL"})
    void fifoIsIndependentOfPortfolioAndKeepsStatus(String status) {
        var b = mock(BotTradingPnlSummary.class);
        when(b.getUserId()).thenReturn(1L);
        when(b.getExchange()).thenReturn("UPBIT");
        when(b.getAccountingStatus()).thenReturn(status);
        when(b.getRecognizedRealizedPnlKrw()).thenReturn(new BigDecimal("-12345.6789000000"));
        when(b.getCalculatedAt()).thenReturn(NOW.minusSeconds(600));
        when(repository.botPnl(SCOPE)).thenReturn(Optional.of(b));
        var result = service.summary();
        assertThat(result.portfolio()).isNull();
        assertThat(result.botPnl().accountingStatus()).isEqualTo(status);
        assertThat(result.botPnl().recognizedRealizedPnlKrw()).isEqualTo("-12345.6789000000");
        assertThat(result.botPnl().scope().amountCurrency()).isEqualTo("KRW");
        assertThat(result.botPnl().sourceLastUpdatedAt()).isNull();
    }
    @Test void positionsUseOnlySelectedSnapshotAndKeepUnknownPrice() {
        portfolio("PARTIAL");
        var p = mock(PortfolioPositionSnapshot.class);
        when(p.getCurrency()).thenReturn("APENFT");
        when(p.getValuationStatus()).thenReturn("UNPRICED");
        when(p.getTotalQuantity()).thenReturn(new BigDecimal("0.1234567890"));
        when(repository.positions(SCOPE, BIG)).thenReturn(List.of(p));
        var result = service.positions().items().getFirst();
        assertThat(result.market()).isNull();
        assertThat(result.markPrice()).isNull();
        assertThat(result.totalQuantity()).isEqualTo("0.1234567890");
        assertThat(result.valuationStatus()).isEqualTo("UNPRICED");
        verify(repository).positions(SCOPE, BIG);
    }
    @Test void inactiveAndMissingUserDenyEveryReadBeforeDataQueries() {
        when(tradingScope.resolve()).thenThrow(new AccessDeniedException("Unavailable"));
        assertThatThrownBy(service::summary).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(service::positions).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service.performance(new OverviewQuery.Performance(null, null, 0, 90)))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service.recommendations(new OverviewQuery.Recommendations(null, 0, 10)))
                .isInstanceOf(AccessDeniedException.class);
        verifyNoInteractions(repository);
    }
    @Test void databaseFailureIsNotConvertedToEmpty() {
        when(repository.latestPortfolio(SCOPE)).thenThrow(new IllegalStateException("fixture database failure"));
        assertThatThrownBy(service::summary).isInstanceOf(IllegalStateException.class);
    }
    @Test void countMetadataAndReadOnlyIsolation() {
        var query = new OverviewQuery.Performance(null, null, 1, 2);
        when(repository.performanceHistory(SCOPE, query)).thenReturn(List.of());
        when(repository.performanceCount(SCOPE, query)).thenReturn(5L);
        var result = service.performance(query);
        assertThat(result.totalElements()).isEqualTo(5);
        assertThat(result.totalPages()).isEqualTo(3);
        var tx = OverviewReadService.class.getAnnotation(Transactional.class);
        assertThat(tx.readOnly()).isTrue();
        assertThat(tx.isolation()).isEqualTo(Isolation.REPEATABLE_READ);
    }
    @Test void invalidFiltersAndOverflowAreRejected() {
        assertThatThrownBy(() -> new OverviewQuery.Performance(NOW, NOW.minusSeconds(1), 0, 90))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new OverviewQuery.Performance(null, null, Integer.MAX_VALUE, 200))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new OverviewQuery.Recommendations("buy", 0, 10))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new OverviewQuery.Recommendations(null, -1, 10))
                .isInstanceOf(IllegalArgumentException.class);
    }
}
