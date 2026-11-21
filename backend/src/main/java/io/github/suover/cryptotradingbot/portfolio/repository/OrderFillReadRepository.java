package io.github.suover.cryptotradingbot.portfolio.repository;

import com.querydsl.jpa.impl.JPAQueryFactory;
import io.github.suover.cryptotradingbot.portfolio.entity.OrderFillRecord;
import io.github.suover.cryptotradingbot.portfolio.entity.QOrderFillRecord;
import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisQuery.Pagination;
import java.util.List;
import org.springframework.stereotype.Repository;

@Repository
public class OrderFillReadRepository {
    private static final QOrderFillRecord FILL = QOrderFillRecord.orderFillRecord;
    private final JPAQueryFactory query;

    public OrderFillReadRepository(JPAQueryFactory query) {
        this.query = query;
    }

    public List<OrderFillRecord> fills(long orderId, Pagination pagination) {
        return query.selectFrom(FILL).where(FILL.orderLogId.eq(orderId)).orderBy(FILL.id.asc())
                .offset((long) pagination.page() * pagination.size()).limit(pagination.size()).fetch();
    }

    public long count(long orderId) {
        return query.select(FILL.id.count()).from(FILL).where(FILL.orderLogId.eq(orderId)).fetchOne();
    }
}
