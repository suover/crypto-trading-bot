package io.github.suover.cryptotradingbot.portfolio.controller;

import io.github.suover.cryptotradingbot.portfolio.service.PortfolioHistoryService;
import io.github.suover.cryptotradingbot.portfolio.service.PortfolioHistoryService.FillsPage;
import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisQuery.Pagination;
import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisResponses.Order;
import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisResponses.Page;
import java.util.Set;
import org.springframework.util.MultiValueMap;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/portfolio/orders")
public class PortfolioHistoryController {
    private final PortfolioHistoryService history;

    public PortfolioHistoryController(PortfolioHistoryService history) {
        this.history = history;
    }

    @GetMapping
    public Page<Order> orders(@RequestParam MultiValueMap<String, String> params) {
        return history.orders(pagination(params));
    }

    @GetMapping("/{id}/fills")
    public FillsPage fills(@PathVariable String id, @RequestParam MultiValueMap<String, String> params) {
        if (!id.matches("[1-9][0-9]{0,18}")) throw new IllegalArgumentException("Invalid order ID");
        return history.fills(Long.parseLong(id), pagination(params));
    }

    private static Pagination pagination(MultiValueMap<String, String> params) {
        if (!Set.of("page", "size").containsAll(params.keySet())
                || params.values().stream().anyMatch(values -> values.size() != 1)) {
            throw new IllegalArgumentException("Unsupported or repeated parameter");
        }
        int page = params.containsKey("page") ? Integer.parseInt(params.getFirst("page")) : 0;
        int size = params.containsKey("size") ? Integer.parseInt(params.getFirst("size")) : 20;
        return new Pagination(page, size);
    }
}
