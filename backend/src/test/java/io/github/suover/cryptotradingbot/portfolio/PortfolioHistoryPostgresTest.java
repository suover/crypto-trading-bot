package io.github.suover.cryptotradingbot.portfolio;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.nullValue;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import io.github.suover.cryptotradingbot.support.PostgresFixtureTest;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.web.servlet.MockMvc;

class PortfolioHistoryPostgresTest extends PostgresFixtureTest {
    private static final String ORDERS = "/api/v1/portfolio/orders";
    @Autowired MockMvc mvc;

    @Test void historyUsesStablePagesOfOwnedOrdersAndSeparatesRequestedAndExecutedValues() throws Exception {
        var session = adminSession(mvc, "fixture-other");
        mvc.perform(get(ORDERS).session(session).param("size", "2"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.scope.userId").value("20"))
                .andExpect(jsonPath("$.totalElements").value(3)).andExpect(jsonPath("$.totalPages").value(2))
                .andExpect(jsonPath("$.items[0].id").value("9703"))
                .andExpect(jsonPath("$.items[0].tradingMode").value("MOCK"))
                .andExpect(jsonPath("$.items[1].status").value("LIVE_UNKNOWN"))
                .andExpect(jsonPath("$.items[1].executedFundsKrw").value(nullValue()));
        var body = mvc.perform(get(ORDERS).session(session).param("page", "1").param("size", "2"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.items[0].id").value("9701"))
                .andExpect(jsonPath("$.items[0].status").value("LIVE_EXECUTED_CANCELLED"))
                .andExpect(jsonPath("$.items[0].quantity").value("2.0000000000"))
                .andExpect(jsonPath("$.items[0].executedQuantity").value("1.0000000000"))
                .andExpect(jsonPath("$.items[0].executedFundsKrw").value("1500.0000000000"))
                .andReturn().getResponse().getContentAsString();
        assertThat(body).doesNotContain("rawResponse", "exchangeOrderId", "errorMessage", "fixture-private");
        mvc.perform(get(ORDERS).session(adminSession(mvc)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.totalElements").value(0));
    }

    @Test void fillsAreBoundToVerifiedParentAndExposeOnlyRecordedExecutionData() throws Exception {
        var session = adminSession(mvc, "fixture-other");
        var body = mvc.perform(get(ORDERS + "/9701/fills").session(session).param("size", "1"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.scope.userId").value("20"))
                .andExpect(jsonPath("$.orderId").value("9701"))
                .andExpect(jsonPath("$.totalElements").value(2)).andExpect(jsonPath("$.totalPages").value(2))
                .andExpect(jsonPath("$.items[0].id").value("9801"))
                .andExpect(jsonPath("$.items[0].quantity").value("0.2500000000"))
                .andExpect(jsonPath("$.items[0].price").value("1500.0000000000"))
                .andExpect(jsonPath("$.items[0].fundsKrw").value("375.0000000000"))
                .andExpect(jsonPath("$.items[0].recordedAt").value("2026-01-01T00:03:00Z"))
                .andReturn().getResponse().getContentAsString();
        assertThat(body).doesNotContain("rawData", "exchangeTradeId", "fee", "filledAt", "fixture-private");
        mvc.perform(get(ORDERS + "/9701/fills").session(session).param("size", "1").param("page", "1"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items[0].id").value("9802"));
        mvc.perform(get(ORDERS + "/9702/fills").session(session))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items").isEmpty());
        mvc.perform(get(ORDERS + "/9701/fills").session(adminSession(mvc))).andExpect(status().isNotFound());
        // Wrong user, exchange, market, mismatched approval, and nonexistent order are indistinguishable.
        for (long invalid : List.of(9704L, 9705L, 9706L, 9707L, 999999L)) {
            mvc.perform(get(ORDERS + "/" + invalid + "/fills").session(session))
                    .andExpect(status().isNotFound()).andExpect(jsonPath("$.code").value("NOT_FOUND"));
        }
    }

    @Test void bothReadsRequireASessionAndRejectScopeOverridesInvalidPaginationAndWrites() throws Exception {
        var session = adminSession(mvc, "fixture-other");
        for (String path : List.of(ORDERS, ORDERS + "/9701/fills")) {
            mvc.perform(get(path)).andExpect(status().isUnauthorized());
            mvc.perform(get(path).with(user("viewer").roles("VIEWER"))).andExpect(status().isForbidden());
            mvc.perform(get(path).session(session).param("userId", "1")).andExpect(status().isBadRequest());
            mvc.perform(get(path).session(session).param("page", "0", "1")).andExpect(status().isBadRequest());
            mvc.perform(get(path).session(session).param("size", "101")).andExpect(status().isBadRequest());
            mvc.perform(get(path).session(session).param("page", "-1")).andExpect(status().isBadRequest());
            mvc.perform(post(path).session(session).with(csrf())).andExpect(status().isForbidden());
            mvc.perform(delete(path).session(session).with(csrf())).andExpect(status().isForbidden());
        }
        for (String invalid : List.of("0", "-2", "orders", "9223372036854775808")) {
            mvc.perform(get(ORDERS + "/" + invalid + "/fills").session(session)).andExpect(status().isBadRequest());
        }
    }
}
