package io.github.suover.cryptotradingbot.portfolio.service;

import io.github.suover.cryptotradingbot.global.security.CurrentTradingScope;
import io.github.suover.cryptotradingbot.portfolio.entity.OrderFillRecord;
import io.github.suover.cryptotradingbot.portfolio.repository.OrderFillReadRepository;
import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisQuery.Pagination;
import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisResponses.*;
import io.github.suover.cryptotradingbot.tradeanalysis.repository.AnalysisReadRepository;
import java.time.Clock;
import java.time.Instant;
import java.util.List;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
public class PortfolioHistoryService {
    public record Fill(String id, String price, String quantity, String fundsKrw, Instant recordedAt) {
        static Fill from(OrderFillRecord fill) {
            return new Fill(fill.getId().toString(), fill.getPrice().toPlainString(),
                    fill.getVolume().toPlainString(), fill.getFundsKrw().toPlainString(), fill.getRecordedAt());
        }
    }
    public record FillsPage(Scope scope, String orderId, List<Fill> items, int page, int size,
                            long totalElements, long totalPages, Instant generatedAt) {}

    private final CurrentTradingScope tradingScope;
    private final AnalysisReadRepository orders;
    private final OrderFillReadRepository fills;
    private final Clock clock;

    public PortfolioHistoryService(CurrentTradingScope tradingScope, AnalysisReadRepository orders,
                                  OrderFillReadRepository fills, Clock clock) {
        this.tradingScope = tradingScope;
        this.orders = orders;
        this.fills = fills;
        this.clock = clock;
    }

    public Page<Order> orders(Pagination pagination) {
        var scope = tradingScope.resolve();
        var rows = orders.orders(scope, pagination).stream().map(Order::from).toList();
        long total = orders.orderCount(scope);
        return new Page<>(responseScope(scope), rows, pagination.page(), pagination.size(), total,
                Math.ceilDiv(total, pagination.size()), clock.instant());
    }

    public FillsPage fills(long orderId, Pagination pagination) {
        var scope = tradingScope.resolve();
        // Fill rows only carry the parent FK. Verify that parent in this same repeatable-read snapshot.
        if (!orders.containsOrder(scope, orderId)) throw new OrderNotFoundException();
        var rows = fills.fills(orderId, pagination).stream().map(Fill::from).toList();
        long total = fills.count(orderId);
        return new FillsPage(responseScope(scope), Long.toString(orderId), rows, pagination.page(), pagination.size(),
                total, Math.ceilDiv(total, pagination.size()), clock.instant());
    }

    private static Scope responseScope(CurrentTradingScope.Scope scope) {
        return new Scope(Long.toString(scope.tradingUserId()), scope.exchange(), scope.quoteAsset());
    }
}
