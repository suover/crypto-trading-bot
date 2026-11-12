package io.github.suover.cryptotradingbot;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import io.github.suover.cryptotradingbot.global.security.SecurityConfiguration;
import io.github.suover.cryptotradingbot.research.controller.ResearchController;
import io.github.suover.cryptotradingbot.research.dto.ResearchResponses.*;
import io.github.suover.cryptotradingbot.research.service.*;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(controllers = ResearchController.class, properties = {
        "backend.trading-user-id=1", "backend.exchange=UPBIT", "backend.quote-asset=KRW",
        "backend.admin-username=fixture-admin",
        "backend.admin-password-hash=$2a$10$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
})
@Import(SecurityConfiguration.class)
class ResearchControllerTest {
    @Autowired MockMvc mvc;
    @MockitoBean ResearchReadService service;
    static final Scope SCOPE = new Scope(1, "UPBIT", "KRW");
    static final Instant NOW = Instant.parse("2026-01-01T00:00:00Z");

    @Test void summary() throws Exception {
        when(service.summary()).thenReturn(new Summary(SCOPE, 0, 0, 0, null, null, NOW));
        mvc.perform(get("/api/v1/research/summary").with(user("admin").roles("ADMIN")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.candidateCount").value(0))
                .andExpect(jsonPath("$.latestSnapshotId").isEmpty())
                .andExpect(jsonPath("$.generatedAt").value("2026-01-01T00:00:00Z"));
    }
    @Test void candidateList() throws Exception {
        when(service.candidates(any())).thenReturn(new Page<>(List.of(), 0, 20, 0, 0, NOW));
        mvc.perform(get("/api/v1/research/candidates").with(user("admin").roles("ADMIN")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items").isEmpty())
                .andExpect(jsonPath("$.totalElements").value(0)).andExpect(jsonPath("$.size").value(20));
    }
    @Test void detail() throws Exception {
        when(service.candidate(1)).thenReturn(new CandidateDetail(1L, "momentum", NOW, "UPBIT", "KRW",
                7, 2L, java.util.Map.of("momentum", java.math.BigDecimal.ONE),
                new RegistrationContext("v1", "baseline", 2L, NOW, NOW), false, null, NOW));
        mvc.perform(get("/api/v1/research/candidates/1").with(user("admin").roles("ADMIN")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.policyName").value("momentum"))
                .andExpect(jsonPath("$.scenarioDefinitionSignature").doesNotExist())
                .andExpect(jsonPath("$.gateChecks").doesNotExist());
    }
    @Test void snapshots() throws Exception {
        when(service.snapshots(0, 20)).thenReturn(new Page<>(List.of(), 0, 20, 0, 0, NOW));
        mvc.perform(get("/api/v1/research/snapshots").with(user("admin").roles("ADMIN")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items").isArray());
    }
    @Test void automation() throws Exception {
        when(service.automationStatus()).thenReturn(new AutomationStatus(SCOPE, false, List.of(), NOW));
        mvc.perform(get("/api/v1/research/automation-status").with(user("admin").roles("ADMIN")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.observed").value(false));
    }
    @ParameterizedTest @ValueSource(strings = {"summary", "candidates", "candidates/1", "snapshots", "automation-status"})
    void requiresAdmin(String path) throws Exception {
        mvc.perform(get("/api/v1/research/" + path)).andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("UNAUTHENTICATED"));
        mvc.perform(get("/api/v1/research/" + path).with(user("reader").roles("USER")))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value("FORBIDDEN"));
    }
    @Test void invalidPassword() throws Exception {
        mvc.perform(get("/api/v1/research/summary").with(httpBasic("fixture-admin", "wrong")))
                .andExpect(status().isUnauthorized());
    }
    @ParameterizedTest @ValueSource(strings = {
        "?size=101", "?page=-1", "?size=0", "?registeredFrom=invalid", "?shadowEnrolled=invalid",
        "?sort=password,desc", "?registeredFrom=2026-02-01T00:00:00Z&registeredTo=2026-01-01T00:00:00Z",
        "?userId=2", "?exchange=BINANCE"
    })
    void badQuery(String query) throws Exception {
        mvc.perform(get("/api/v1/research/candidates" + query).with(user("admin").roles("ADMIN")))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.code").value("INVALID_REQUEST"));
        verifyNoInteractions(service);
    }
    @Test void notFoundAndDatabaseFailureAreDifferent() throws Exception {
        when(service.candidate(999)).thenThrow(new ResearchNotFoundException());
        mvc.perform(get("/api/v1/research/candidates/999").with(user("admin").roles("ADMIN")))
                .andExpect(status().isNotFound());
        when(service.summary()).thenThrow(new RuntimeException("secret database failure details"));
        mvc.perform(get("/api/v1/research/summary").with(user("admin").roles("ADMIN")))
                .andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.message").value("Request could not be completed"));
    }
    @Test void jakartaValidationRejectsInvalidIdAndOversizedKeyword() throws Exception {
        mvc.perform(get("/api/v1/research/candidates/0").with(user("admin").roles("ADMIN")))
                .andExpect(status().isBadRequest());
        mvc.perform(get("/api/v1/research/candidates").param("keyword", "x".repeat(81))
                .with(user("admin").roles("ADMIN"))).andExpect(status().isBadRequest());
        mvc.perform(get("/api/v1/research/snapshots").param("size", "101")
                .with(user("admin").roles("ADMIN"))).andExpect(status().isBadRequest());
        verifyNoInteractions(service);
    }
    @Test void writesAndUnknownEndpointsDenied() throws Exception {
        for (var method : List.of(org.springframework.http.HttpMethod.POST,
                org.springframework.http.HttpMethod.PUT, org.springframework.http.HttpMethod.PATCH,
                org.springframework.http.HttpMethod.DELETE)) {
            mvc.perform(request(method, "/api/v1/research/candidates").with(user("admin").roles("ADMIN")))
                    .andExpect(status().isForbidden());
        }
        mvc.perform(get("/actuator/env").with(user("admin").roles("ADMIN"))).andExpect(status().isForbidden());
        verifyNoInteractions(service);
    }
}
