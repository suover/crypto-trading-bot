package io.github.suover.cryptotradingbot.overview.repository;

import com.querydsl.core.BooleanBuilder;
import com.querydsl.jpa.impl.JPAQueryFactory;
import io.github.suover.cryptotradingbot.global.security.CurrentTradingScope.Scope;
import io.github.suover.cryptotradingbot.overview.dto.OverviewQuery;
import io.github.suover.cryptotradingbot.overview.entity.*;
import java.util.List;
import java.util.Optional;
import org.springframework.stereotype.Repository;

@Repository
public class OverviewReadRepository {
    private static final QPortfolioSnapshot S = QPortfolioSnapshot.portfolioSnapshot;
    private static final QPortfolioPositionSnapshot P = QPortfolioPositionSnapshot.portfolioPositionSnapshot;
    private static final QPortfolioPerformanceSnapshot F = QPortfolioPerformanceSnapshot.portfolioPerformanceSnapshot;
    private static final QBotTradingPnlSummary B = QBotTradingPnlSummary.botTradingPnlSummary;
    private static final QTradeRecommendation R = QTradeRecommendation.tradeRecommendation;
    public static final int MAX_POSITIONS = 1000;
    private final JPAQueryFactory query;

    public OverviewReadRepository(JPAQueryFactory query) {
        this.query = query;
    }
    private BooleanBuilder portfolioScope(Scope scope) {
        return new BooleanBuilder(S.userId.eq(scope.tradingUserId()))
                .and(S.exchange.eq(scope.exchange())).and(S.quoteAsset.eq(scope.quoteAsset()));
    }
    public Optional<PortfolioSnapshot> latestPortfolio(Scope scope) {
        return Optional.ofNullable(query.selectFrom(S).where(portfolioScope(scope))
                .orderBy(S.capturedAt.desc(), S.id.desc()).fetchFirst());
    }
    public List<PortfolioPositionSnapshot> positions(Scope scope, long snapshotId) {
        // FK ownership plus child exchange: never trust a caller-supplied snapshot ID alone.
        var rows = query.select(P).from(P).join(S).on(P.portfolioSnapshotId.eq(S.id))
                .where(portfolioScope(scope), S.id.eq(snapshotId), P.exchange.eq(S.exchange))
                .orderBy(P.marketValueKrw.desc().nullsLast(), P.currency.asc(), P.id.asc())
                .limit(MAX_POSITIONS + 1L).fetch();
        if (rows.size() > MAX_POSITIONS) throw new IllegalStateException("Position read bound exceeded");
        return rows;
    }
    private BooleanBuilder performanceScope(Scope scope) {
        return portfolioScope(scope).and(F.userId.eq(S.userId)).and(F.exchange.eq(S.exchange));
    }
    public Optional<PortfolioPerformanceSnapshot> performance(Scope scope, long snapshotId) {
        return Optional.ofNullable(query.select(F).from(F).join(S).on(F.portfolioSnapshotId.eq(S.id))
                .where(performanceScope(scope), S.id.eq(snapshotId)).fetchOne());
    }
    private BooleanBuilder performanceFilter(Scope scope, OverviewQuery.Performance filter) {
        var where = performanceScope(scope);
        if (filter.from() != null) where.and(F.periodEndAt.goe(filter.from()));
        if (filter.to() != null) where.and(F.periodEndAt.loe(filter.to()));
        return where;
    }
    public List<PortfolioPerformanceSnapshot> performanceHistory(Scope scope, OverviewQuery.Performance filter) {
        return query.select(F).from(F).join(S).on(F.portfolioSnapshotId.eq(S.id))
                .where(performanceFilter(scope, filter)).orderBy(F.periodEndAt.desc(), F.id.desc())
                .offset((long) filter.page() * filter.size()).limit(filter.size()).fetch();
    }
    public long performanceCount(Scope scope, OverviewQuery.Performance filter) {
        return query.select(F.id.count()).from(F).join(S).on(F.portfolioSnapshotId.eq(S.id))
                .where(performanceFilter(scope, filter)).fetchOne();
    }
    public Optional<BotTradingPnlSummary> botPnl(Scope scope) {
        // This is user/exchange accounting in KRW, not a quote-filtered portfolio total.
        if (!scope.quoteAsset().equals("KRW")) return Optional.empty();
        return Optional.ofNullable(query.selectFrom(B).where(B.userId.eq(scope.tradingUserId()),
                B.exchange.eq(scope.exchange())).fetchOne());
    }
    private BooleanBuilder recommendationFilter(Scope scope, OverviewQuery.Recommendations filter) {
        // Only Upbit's QUOTE-BASE convention is proven by the current Python implementation.
        // Do not infer a quote from concatenated symbols at other exchanges.
        var where = new BooleanBuilder(R.userId.eq(scope.tradingUserId()))
                .and(R.exchange.eq(scope.exchange())).and(R.exchange.eq("UPBIT"))
                .and(R.market.startsWith(scope.quoteAsset() + "-"))
                .and(R.market.length().gt(scope.quoteAsset().length() + 1));
        if (filter.action() != null) where.and(R.action.eq(filter.action()));
        return where;
    }
    public List<TradeRecommendation> recommendations(Scope scope, OverviewQuery.Recommendations filter) {
        return query.selectFrom(R).where(recommendationFilter(scope, filter))
                .orderBy(R.createdAt.desc(), R.id.desc())
                .offset((long) filter.page() * filter.size()).limit(filter.size()).fetch();
    }
    public long recommendationCount(Scope scope, OverviewQuery.Recommendations filter) {
        return query.select(R.id.count()).from(R).where(recommendationFilter(scope, filter)).fetchOne();
    }
}
