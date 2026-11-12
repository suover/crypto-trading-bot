package io.github.suover.cryptotradingbot;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import io.github.suover.cryptotradingbot.global.config.BackendProperties;
import io.github.suover.cryptotradingbot.research.dto.CandidateSearch;
import io.github.suover.cryptotradingbot.research.dto.ResearchResponses.*;
import io.github.suover.cryptotradingbot.research.entity.*;
import io.github.suover.cryptotradingbot.research.repository.*;
import io.github.suover.cryptotradingbot.research.service.*;
import java.math.BigDecimal;
import java.time.*;
import java.util.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.security.access.AccessDeniedException;

class ResearchReadServiceTest {
    static final String HASH = "$2a$10$" + "a".repeat(53);
    static final Instant NOW = Instant.parse("2026-01-01T00:00:00Z");
    ResearchReadRepository repository = mock(ResearchReadRepository.class);
    TradingUserRepository users = mock(TradingUserRepository.class);
    ResearchReadService service = new ResearchReadService(repository, users,
            new BackendProperties(1, "UPBIT", "KRW", "fixture-admin", HASH), Clock.fixed(NOW, ZoneOffset.UTC));

    @BeforeEach void activeUser() {
        TradingUser user = mock(TradingUser.class);
        when(user.getIsActive()).thenReturn(true);
        when(users.findById(1L)).thenReturn(Optional.of(user));
    }
    @Test void emptySummaryDistinguishesNullFromZero() {
        var result = service.summary();
        assertThat(result.candidateCount()).isZero();
        assertThat(result.latestSnapshotId()).isNull();
        assertThat(result.latestSnapshotCapturedAt()).isNull();
        assertThat(result.generatedAt()).isEqualTo(NOW);
        assertThat(service.automationStatus().observed()).isFalse();
    }
    @Test void normalSummaryMapsLatestData() {
        when(repository.candidateCount()).thenReturn(5L);
        when(repository.snapshotCount()).thenReturn(12L);
        when(repository.enrollmentCount()).thenReturn(2L);
        when(repository.latestSnapshot()).thenReturn(Optional.of(new SnapshotItem(
                99L, NOW.minusSeconds(60), NOW, "baseline", 20, 10, 7, 8)));
        var result = service.summary();
        assertThat(result.candidateCount()).isEqualTo(5);
        assertThat(result.shadowEnrollmentCount()).isEqualTo(2);
        assertThat(result.snapshotCount()).isEqualTo(12);
        assertThat(result.latestSnapshotId()).isEqualTo(99);
        assertThat(result.latestSnapshotCapturedAt()).isEqualTo(NOW.minusSeconds(60));
    }
    @Test void missingOrOtherUserCandidateIsNotFound() {
        assertThatThrownBy(() -> service.candidate(999)).isInstanceOf(ResearchNotFoundException.class);
        verify(repository).candidate(999);
    }
    @Test void inactiveOrMissingTradingUserFailsClosed() {
        when(users.findById(1L)).thenReturn(Optional.empty());
        assertThatThrownBy(service::summary).isInstanceOf(AccessDeniedException.class);
        verifyNoInteractions(repository);
    }
    @Test void dtoExposesOnlyRequiredCandidateFieldsAndKeepsDecimals() {
        var c = mock(ResearchCandidate.class);
        when(c.getId()).thenReturn(7L);
        when(c.getComponentWeights()).thenReturn(Map.of("liquidity", new BigDecimal("0.12345678901234567890123")));
        when(repository.candidate(7)).thenReturn(Optional.of(c));
        var result = service.candidate(7);
        assertThat(result.id()).isEqualTo(7);
        assertThat(result.componentWeights().get("liquidity")).isEqualByComparingTo("0.12345678901234567890123");
        assertThat(result.shadowEnrolled()).isFalse();
        assertThat(result.enrollment()).isNull();
    }
    @Test void searchIsPassedThroughAndPaginationMetadataIsAccurate() {
        var search = new CandidateSearch("momentum", NOW, NOW, false, 1, 2, "policyName,asc");
        when(repository.candidateCount(search)).thenReturn(5L);
        var result = service.candidates(search);
        assertThat(result.totalPages()).isEqualTo(3);
        assertThat(result.page()).isEqualTo(1);
        verify(repository).candidates(search);
    }
    @ParameterizedTest @CsvSource({"-1,20", "0,0", "0,101"})
    void invalidPages(int page, int size) {
        assertThatThrownBy(() -> new CandidateSearch(null, null, null, null, page, size, null))
                .isInstanceOf(IllegalArgumentException.class);
    }
    @Test void invalidDatesAndSortRejected() {
        assertThatThrownBy(() -> new CandidateSearch(null, NOW, NOW.minusSeconds(1), null, 0, 20, null))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new CandidateSearch(null, null, null, null, 0, 20, "userId,desc"))
                .isInstanceOf(IllegalArgumentException.class);
    }
    @Test void databaseFailureIsNotAnEmptyResult() {
        when(repository.candidateCount()).thenThrow(new IllegalStateException("fixture failure"));
        assertThatThrownBy(service::summary).isInstanceOf(IllegalStateException.class);
    }
    @Test void credentialsAreRequiredAndNeverInToString() {
        assertThatThrownBy(() -> new BackendProperties(1, "UPBIT", "KRW", "", HASH))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new BackendProperties(1, "UPBIT", "KRW", "admin", "plaintext"))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new BackendProperties(0, "UPBIT", "KRW", "admin", HASH))
                .isInstanceOf(IllegalArgumentException.class);
        assertThat(new BackendProperties(1, "UPBIT", "KRW", "admin", HASH).toString()).doesNotContain(HASH);
    }
}
