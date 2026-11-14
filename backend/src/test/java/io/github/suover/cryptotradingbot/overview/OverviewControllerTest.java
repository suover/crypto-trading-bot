package io.github.suover.cryptotradingbot.overview;

import static org.mockito.Mockito.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import io.github.suover.cryptotradingbot.global.security.SecurityConfiguration;
import io.github.suover.cryptotradingbot.overview.controller.OverviewController;
import io.github.suover.cryptotradingbot.overview.dto.OverviewResponses.*;
import io.github.suover.cryptotradingbot.overview.entity.PortfolioSnapshot;
import io.github.suover.cryptotradingbot.overview.service.OverviewReadService;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.HttpMethod;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(controllers = OverviewController.class)
@Import(SecurityConfiguration.class)
class OverviewControllerTest {
    @org.springframework.test.context.bean.override.mockito.MockitoBean
    org.springframework.security.core.userdetails.UserDetailsService dashboardAccounts;
    @Autowired MockMvc mvc;
    @MockitoBean OverviewReadService service;
    static final Scope SCOPE = new Scope("1", "UPBIT", "KRW");
    static final Instant NOW = Instant.parse("2026-01-01T00:00:00Z");
    @Test void fourReadEndpointsUseTheirOwnDtosAndDefaults() throws Exception {
        when(service.summary()).thenReturn(new Summary(SCOPE, null, null, null, NOW));
        when(service.positions()).thenReturn(new Positions(SCOPE, null, List.of(), NOW));
        when(service.performance(any())).thenReturn(new Page<>(SCOPE, List.of(), 0, 90, 0, 0, NOW));
        when(service.recommendations(any())).thenReturn(new Page<>(SCOPE, List.of(), 0, 10, 0, 0, NOW));
        for (String route : List.of("summary", "positions", "performance", "recommendations")) {
            mvc.perform(get("/api/v1/overview/" + route).with(user("admin").roles("ADMIN")))
                    .andExpect(status().isOk()).andExpect(jsonPath("$.scope.userId").value("1"))
                    .andExpect(jsonPath("$.generatedAt").value("2026-01-01T00:00:00Z"));
        }
        verify(service).performance(new io.github.suover.cryptotradingbot.overview.dto.OverviewQuery.Performance(null, null, 0, 90));
        verify(service).recommendations(new io.github.suover.cryptotradingbot.overview.dto.OverviewQuery.Recommendations(null, 0, 10));
    }
    @Test void decimalsAndIdsAreStringsAndPartialIsNotRepaired() throws Exception {
        var p = mock(PortfolioSnapshot.class);
        when(p.getId()).thenReturn(9007199254740993L);
        when(p.getValuationStatus()).thenReturn("PARTIAL");
        when(p.getKnownTotalValueKrw()).thenReturn(new BigDecimal("12345678901234567890.1234567890"));
        when(p.getCashTotalKrw()).thenReturn(new BigDecimal("0.0000000000"));
        var portfolio = Portfolio.from(p);
        when(service.summary()).thenReturn(new Summary(SCOPE, portfolio, null, null, NOW));
        mvc.perform(get("/api/v1/overview/summary").with(user("admin").roles("ADMIN")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.portfolio.snapshotId").isString())
                .andExpect(jsonPath("$.portfolio.snapshotId").value("9007199254740993"))
                .andExpect(jsonPath("$.portfolio.knownTotalValueKrw").value("12345678901234567890.1234567890"))
                .andExpect(jsonPath("$.portfolio.totalValueKrw").isEmpty())
                .andExpect(jsonPath("$.portfolio.cashTotalKrw").value("0.0000000000"))
                .andExpect(jsonPath("$.portfolio.valuationStatus").value("PARTIAL"));
    }
    @ParameterizedTest @ValueSource(strings = {"summary", "positions", "performance", "recommendations"})
    void authenticationAndRoleRequired(String path) throws Exception {
        String url = "/api/v1/overview/" + path;
        mvc.perform(get(url)).andExpect(status().isUnauthorized());
        mvc.perform(get(url).with(httpBasic("fixture-admin", "wrong"))).andExpect(status().isUnauthorized());
        mvc.perform(get(url).with(user("viewer").roles("VIEWER"))).andExpect(status().isForbidden());
        verifyNoInteractions(service);
    }
    @ParameterizedTest @ValueSource(strings = {
            "summary?userId=2", "positions?exchange=BINANCE", "performance?size=201",
            "performance?page=-1", "performance?page=2147483648", "performance?page=2147483647&size=200",
            "performance?from=invalid", "performance?from=2026-01-02T00:00:00Z&to=2026-01-01T00:00:00Z",
            "performance?sort=id,asc", "recommendations?size=51", "recommendations?size=0",
            "recommendations?action=buy", "recommendations?action=", "recommendations?quoteAsset=BTC",
            "recommendations?page=NaN", "recommendations?page=0&page=1", "performance?size="
    })
    void invalidInputIs400(String path) throws Exception {
        mvc.perform(get("/api/v1/overview/" + path).with(user("admin").roles("ADMIN")))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.code").value("INVALID_REQUEST"));
        verifyNoInteractions(service);
    }
    @Test void inactiveScopeAndDatabaseFailureAreSanitized() throws Exception {
        when(service.summary()).thenThrow(new AccessDeniedException("private user"));
        mvc.perform(get("/api/v1/overview/summary").with(user("admin").roles("ADMIN")))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.message").value("Access denied"));
        doThrow(new IllegalStateException("password=private JDBC failure")).when(service).summary();
        mvc.perform(get("/api/v1/overview/summary").with(user("admin").roles("ADMIN")))
                .andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.message").value("Request could not be completed"))
                .andExpect(jsonPath("$.stackTrace").doesNotExist());
    }
    @ParameterizedTest @ValueSource(strings = {"summary", "positions", "performance", "recommendations"})
    void writeMethodsRemainForbidden(String path) throws Exception {
        for (HttpMethod method : List.of(HttpMethod.POST, HttpMethod.PUT, HttpMethod.PATCH, HttpMethod.DELETE)) {
            mvc.perform(request(method, "/api/v1/overview/" + path).with(user("admin").roles("ADMIN")))
                    .andExpect(status().isForbidden());
        }
        verifyNoInteractions(service);
    }
    @Test void undefinedEndpointsAreForbidden() throws Exception {
        mvc.perform(get("/api/v1/overview/orders").with(user("admin").roles("ADMIN"))).andExpect(status().isForbidden());
        mvc.perform(get("/api/v1/overview/summary/1").with(user("admin").roles("ADMIN"))).andExpect(status().isForbidden());
        verifyNoInteractions(service);
    }
}
