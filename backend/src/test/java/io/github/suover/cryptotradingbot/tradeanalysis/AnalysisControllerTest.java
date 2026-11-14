package io.github.suover.cryptotradingbot.tradeanalysis;

import static org.mockito.Mockito.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import io.github.suover.cryptotradingbot.global.security.SecurityConfiguration;
import io.github.suover.cryptotradingbot.tradeanalysis.controller.AnalysisController;
import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisQuery;
import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisResponses.*;
import io.github.suover.cryptotradingbot.tradeanalysis.service.*;
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

@WebMvcTest(controllers = AnalysisController.class)
@Import(SecurityConfiguration.class)
class AnalysisControllerTest {
    static final String ROOT = "/api/v1/ai-trade-analysis/recommendations";
    @org.springframework.test.context.bean.override.mockito.MockitoBean
    org.springframework.security.core.userdetails.UserDetailsService dashboardAccounts;
    @Autowired MockMvc mvc;
    @MockitoBean AnalysisReadService service;
    @Test void listDefaultsAndExplicitFiltersReachTypedContract() throws Exception {
        var now = Instant.parse("2026-01-01T00:00:00Z");
        when(service.recommendations(any())).thenReturn(
                new Page<>(new Scope("1", "UPBIT", "KRW"), List.of(), 0, 20, 0, 0, now));
        mvc.perform(get(ROOT).with(user("admin").roles("ADMIN"))).andExpect(status().isOk())
                .andExpect(jsonPath("$.scope.userId").value("1")).andExpect(jsonPath("$.items").isEmpty());
        verify(service).recommendations(new AnalysisQuery.Search(null, null, null, null, 0, 20));
        mvc.perform(get(ROOT).param("from", now.toString()).param("to", now.toString())
                .param("market", "KRW-BTC").param("action", "HOLD").param("page", "2").param("size", "10")
                .with(user("admin").roles("ADMIN"))).andExpect(status().isOk());
        verify(service).recommendations(new AnalysisQuery.Search(now, now, "KRW-BTC", "HOLD", 2, 10));
    }
    @Test void detailAndCandidateDefaultsDoNotAcceptScopeFromClient() throws Exception {
        mvc.perform(get(ROOT + "/9007199254741093").with(user("admin").roles("ADMIN"))).andExpect(status().isOk());
        verify(service).detail(9007199254741093L);
        mvc.perform(get(ROOT + "/5/candidates").with(user("admin").roles("ADMIN"))).andExpect(status().isOk());
        verify(service).candidates(5, new AnalysisQuery.Pagination(0, 20));
    }
    @ParameterizedTest @ValueSource(strings = {"", "/1", "/1/candidates"})
    void everyRouteRequiresAdminAndRejectsWrites(String suffix) throws Exception {
        mvc.perform(get(ROOT + suffix)).andExpect(status().isUnauthorized());
        mvc.perform(get(ROOT + suffix).with(httpBasic("fixture-admin", "wrong"))).andExpect(status().isUnauthorized());
        mvc.perform(get(ROOT + suffix).with(user("viewer").roles("VIEWER"))).andExpect(status().isForbidden());
        for (HttpMethod method : List.of(HttpMethod.POST, HttpMethod.PUT, HttpMethod.PATCH, HttpMethod.DELETE))
            mvc.perform(request(method, ROOT + suffix).with(user("admin").roles("ADMIN"))).andExpect(status().isForbidden());
        verifyNoInteractions(service);
    }
    @ParameterizedTest @ValueSource(strings = {
        "?page=-1", "?size=0", "?size=101", "?page=2147483648", "?page=2147483647&size=100",
        "?page=0&page=1", "?action=buy", "?action=", "?market=KRW-", "?market=KRW-%25",
        "?market=BTC", "?market=", "?from=", "?from=invalid",
        "?from=2026-02-01T00:00:00Z&to=2026-01-01T00:00:00Z",
        "?userId=2", "?exchange=BINANCE", "?quoteAsset=BTC", "?sort=id,asc",
        "/0", "/-1", "/01", "/abc", "/9223372036854775808", "/1?raw=true",
        "/1/candidates?page=-1", "/1/candidates?size=101", "/1/candidates?pipelineRunId=other"
    })
    void invalidInputsFailBeforeRepositoryWork(String suffix) throws Exception {
        mvc.perform(get(ROOT + suffix).with(user("admin").roles("ADMIN"))).andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_REQUEST"));
        verifyNoInteractions(service);
    }
    @Test void sanitizedErrorsAndMissingOrOutOfScopeAreIndistinguishable() throws Exception {
        when(service.detail(1)).thenThrow(new AnalysisNotFoundException());
        mvc.perform(get(ROOT + "/1").with(user("admin").roles("ADMIN"))).andExpect(status().isNotFound())
                .andExpect(jsonPath("$.message").value("Recommendation not found"));
        doThrow(new AccessDeniedException("private scope")).when(service).detail(1);
        mvc.perform(get(ROOT + "/1").with(user("admin").roles("ADMIN"))).andExpect(status().isForbidden());
        doThrow(new IllegalStateException("password=private JDBC error")).when(service).detail(1);
        mvc.perform(get(ROOT + "/1").with(user("admin").roles("ADMIN"))).andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.message").value("Request could not be completed"))
                .andExpect(jsonPath("$.stackTrace").doesNotExist());
    }
    @Test void unimplementedApprovalOrderAndReconciliationPathsAreForbidden() throws Exception {
        for (String suffix : List.of("/1/approve", "/1/orders", "/1/reconcile", "/1/candidates/1"))
            mvc.perform(get(ROOT + suffix).with(user("admin").roles("ADMIN"))).andExpect(status().isForbidden());
        verifyNoInteractions(service);
    }
}
