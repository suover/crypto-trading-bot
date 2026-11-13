package io.github.suover.cryptotradingbot.tradeanalysis.repository;

import com.querydsl.core.BooleanBuilder;
import com.querydsl.jpa.impl.JPAQueryFactory;
import io.github.suover.cryptotradingbot.global.config.BackendProperties;
import io.github.suover.cryptotradingbot.tradeanalysis.dto.AnalysisQuery;
import io.github.suover.cryptotradingbot.tradeanalysis.entity.*;
import java.util.List;
import java.util.Optional;
import org.springframework.stereotype.Repository;

@Repository
public class AnalysisReadRepository {
    private static final QRecommendationRecord R = QRecommendationRecord.recommendationRecord;
    private static final QAnalysisRecord A = QAnalysisRecord.analysisRecord;
    private static final QUniverseCandidateRecord C = QUniverseCandidateRecord.universeCandidateRecord;
    private static final QAnalysisRecord U = new QAnalysisRecord("universeRun");
    private static final QApprovalRecord P = QApprovalRecord.approvalRecord;
    private static final QExecutionAttemptRecord T = QExecutionAttemptRecord.executionAttemptRecord;
    private static final QOrderRecord O = QOrderRecord.orderRecord;
    public static final int MAX_RELATED_RECORDS = 1000;
    private final JPAQueryFactory query;
    private final BackendProperties scope;
    public AnalysisReadRepository(JPAQueryFactory query, BackendProperties scope) {
        this.query = query;
        this.scope = scope;
    }
    private BooleanBuilder owned() {
        return new BooleanBuilder(R.userId.eq(scope.tradingUserId())).and(A.userId.eq(R.userId))
                .and(R.exchange.eq(scope.exchange())).and(R.exchange.eq("UPBIT"))
                .and(R.market.startsWith(scope.quoteAsset() + "-"))
                .and(R.market.length().gt(scope.quoteAsset().length() + 1));
    }
    private BooleanBuilder filter(AnalysisQuery.Search f) {
        var where = owned();
        if (f.from() != null) where.and(R.createdAt.goe(f.from()));
        if (f.to() != null) where.and(R.createdAt.loe(f.to()));
        if (f.market() != null) where.and(R.market.eq(f.market()));
        if (f.action() != null) where.and(R.action.eq(f.action()));
        return where;
    }
    public List<RecommendationRecord> recommendations(AnalysisQuery.Search f) {
        return query.select(R).from(R).join(A).on(R.analysisRunId.eq(A.id)).where(filter(f))
                .orderBy(R.createdAt.desc(), R.id.desc()).offset((long) f.page() * f.size()).limit(f.size()).fetch();
    }
    public long count(AnalysisQuery.Search f) {
        return query.select(R.id.count()).from(R).join(A).on(R.analysisRunId.eq(A.id)).where(filter(f)).fetchOne();
    }
    public Optional<RecommendationRecord> recommendation(long id) {
        return Optional.ofNullable(query.select(R).from(R).join(A).on(R.analysisRunId.eq(A.id))
                .where(owned(), R.id.eq(id)).fetchOne());
    }
    public AnalysisRecord analysis(long id) {
        return query.selectFrom(A).where(A.id.eq(id), A.userId.eq(scope.tradingUserId())).fetchOne();
    }
    public Optional<UniverseCandidateRecord> selectedCandidate(RecommendationRecord r, AnalysisRecord a) {
        if (r.getUniverseCandidateId() == null || a.getPipelineRunId() == null || a.getPipelineRunId().isBlank())
            return Optional.empty();
        return Optional.ofNullable(query.select(C).from(C).join(U).on(C.analysisRunId.eq(U.id))
                .where(C.id.eq(r.getUniverseCandidateId()), C.userId.eq(scope.tradingUserId()),
                        U.userId.eq(C.userId), C.exchange.eq(scope.exchange()), C.quoteAsset.eq(scope.quoteAsset()),
                        C.market.eq(r.getMarket()), U.pipelineRunId.eq(a.getPipelineRunId()),
                        U.runType.eq("MARKET_UNIVERSE"), U.status.eq("SUCCESS")).fetchOne());
    }
    private BooleanBuilder universeScope(long runId) {
        return new BooleanBuilder(C.analysisRunId.eq(runId)).and(C.userId.eq(scope.tradingUserId()))
                .and(U.userId.eq(C.userId)).and(U.runType.eq("MARKET_UNIVERSE")).and(U.status.eq("SUCCESS"))
                .and(C.exchange.eq(scope.exchange())).and(C.quoteAsset.eq(scope.quoteAsset()))
                .and(C.market.startsWith(scope.quoteAsset() + "-"))
                .and(C.market.length().gt(scope.quoteAsset().length() + 1));
    }
    public List<UniverseCandidateRecord> candidates(long runId, AnalysisQuery.Pagination f) {
        return query.select(C).from(C).join(U).on(C.analysisRunId.eq(U.id)).where(universeScope(runId))
                .orderBy(C.rank.asc().nullsLast(), C.id.asc()).offset((long) f.page() * f.size()).limit(f.size()).fetch();
    }
    public long candidateCount(long runId) {
        return query.select(C.id.count()).from(C).join(U).on(C.analysisRunId.eq(U.id))
                .where(universeScope(runId)).fetchOne();
    }
    public List<ApprovalRecord> approvals(long recommendationId) {
        return bounded(query.select(P).from(P).join(R).on(P.recommendationId.eq(R.id))
                .join(A).on(R.analysisRunId.eq(A.id))
                .where(owned(), R.id.eq(recommendationId), P.userId.eq(R.userId))
                .orderBy(P.createdAt.asc(), P.id.asc()).limit(MAX_RELATED_RECORDS + 1L).fetch());
    }
    public Optional<OrderRecord> order(long recommendationId) {
        return Optional.ofNullable(query.select(O).from(O).join(R).on(O.recommendationId.eq(R.id))
                .join(A).on(R.analysisRunId.eq(A.id))
                .leftJoin(P).on(O.approvalRequestId.eq(P.id))
                .where(owned(), R.id.eq(recommendationId), O.userId.eq(R.userId),
                        O.exchange.eq(R.exchange), O.market.eq(R.market),
                        O.approvalRequestId.isNull().or(P.recommendationId.eq(R.id).and(P.userId.eq(R.userId))))
                .fetchOne());
    }
    public List<ExecutionAttemptRecord> attempts(long recommendationId) {
        return bounded(query.select(T).from(T).join(R).on(T.recommendationId.eq(R.id))
                .join(A).on(R.analysisRunId.eq(A.id)).join(P).on(T.approvalRequestId.eq(P.id))
                .leftJoin(O).on(T.orderLogId.eq(O.id))
                .where(owned(), R.id.eq(recommendationId), T.userId.eq(R.userId),
                        P.userId.eq(R.userId), P.recommendationId.eq(R.id),
                        T.orderLogId.isNull().or(O.recommendationId.eq(R.id).and(O.userId.eq(R.userId))
                                .and(O.exchange.eq(R.exchange)).and(O.market.eq(R.market))
                                .and(O.tradingMode.eq(T.tradingMode)).and(O.approvalRequestId.eq(P.id))))
                .orderBy(T.attemptedAt.asc(), T.id.asc()).limit(MAX_RELATED_RECORDS + 1L).fetch());
    }
    private static <T> List<T> bounded(List<T> records) {
        if (records.size() > MAX_RELATED_RECORDS) throw new IllegalStateException("Related read bound exceeded");
        return records;
    }
}
