package io.github.suover.cryptotradingbot.research.repository;

import com.querydsl.core.BooleanBuilder;
import com.querydsl.core.types.OrderSpecifier;
import com.querydsl.core.types.Projections;
import com.querydsl.jpa.JPAExpressions;
import com.querydsl.jpa.impl.JPAQueryFactory;
import io.github.suover.cryptotradingbot.global.security.CurrentTradingScope.Scope;
import io.github.suover.cryptotradingbot.research.dto.CandidateSearch;
import io.github.suover.cryptotradingbot.research.dto.ResearchResponses.*;
import io.github.suover.cryptotradingbot.research.entity.*;
import java.util.List;
import java.util.Optional;
import org.springframework.stereotype.Repository;

@Repository
public class ResearchReadRepository {
    private static final QResearchCandidate C = QResearchCandidate.researchCandidate;
    private static final QShadowEnrollment E = QShadowEnrollment.shadowEnrollment;
    private static final QReplaySnapshot S = QReplaySnapshot.replaySnapshot;
    private static final QAutomationState A = QAutomationState.automationState;
    private final JPAQueryFactory query;

    public ResearchReadRepository(JPAQueryFactory query) {
        this.query = query;
    }

    private BooleanBuilder candidateScope(Scope scope) {
        return new BooleanBuilder(C.userId.eq(scope.tradingUserId()))
                .and(C.exchange.eq(scope.exchange())).and(C.quoteAsset.eq(scope.quoteAsset()));
    }
    private BooleanBuilder snapshotScope(Scope scope) {
        return new BooleanBuilder(S.userId.eq(scope.tradingUserId()))
                .and(S.exchange.eq(scope.exchange())).and(S.quoteAsset.eq(scope.quoteAsset()));
    }
    // Scope both sides, including the immutable research context, rather than trusting a bare FK.
    private BooleanBuilder enrollmentMatch() {
        return new BooleanBuilder(E.candidateId.eq(C.id)).and(E.userId.eq(C.userId))
                .and(E.exchange.eq(C.exchange)).and(E.quoteAsset.eq(C.quoteAsset))
                .and(E.baselinePolicySignature.eq(C.baselinePolicySignature))
                .and(E.datasetSchemaVersion.eq(C.datasetSchemaVersion))
                .and(E.effectiveTopN.eq(C.effectiveTopN));
    }

    public long candidateCount(Scope scope) {
        return query.select(C.id.count()).from(C).where(candidateScope(scope)).fetchOne();
    }
    public long enrollmentCount(Scope scope) {
        return query.select(C.id.count()).from(C).where(candidateScope(scope),
                JPAExpressions.selectOne().from(E).where(enrollmentMatch()).exists()).fetchOne();
    }
    public long snapshotCount(Scope scope) {
        return query.select(S.id.count()).from(S).where(snapshotScope(scope)).fetchOne();
    }
    public Optional<SnapshotItem> latestSnapshot(Scope scope) {
        return Optional.ofNullable(query.select(snapshotProjection()).from(S)
                .where(snapshotScope(scope)).orderBy(S.capturedAt.desc(), S.id.desc()).fetchFirst());
    }

    private BooleanBuilder filter(Scope scope, CandidateSearch search) {
        BooleanBuilder where = candidateScope(scope);
        if (search.keyword() != null && !search.keyword().isEmpty()) {
            where.and(C.scenarioName.containsIgnoreCase(search.keyword()));
        }
        if (search.registeredFrom() != null) where.and(C.registeredAt.goe(search.registeredFrom()));
        if (search.registeredTo() != null) where.and(C.registeredAt.loe(search.registeredTo()));
        if (search.shadowEnrolled() != null) {
            var exists = JPAExpressions.selectOne().from(E).where(enrollmentMatch()).exists();
            where.and(search.shadowEnrolled() ? exists : exists.not());
        }
        return where;
    }

    public long candidateCount(Scope scope, CandidateSearch search) {
        return query.select(C.id.count()).from(C).where(filter(scope, search)).fetchOne();
    }
    public List<CandidateItem> candidates(Scope scope, CandidateSearch search) {
        boolean asc = search.sort().endsWith(",asc");
        OrderSpecifier<?> primary = switch (search.sort().split(",")[0]) {
            case "id" -> asc ? C.id.asc() : C.id.desc();
            case "policyName" -> asc ? C.scenarioName.asc() : C.scenarioName.desc();
            default -> asc ? C.registeredAt.asc() : C.registeredAt.desc();
        };
        return query.select(Projections.constructor(CandidateItem.class, C.id, C.scenarioName,
                        C.registeredAt, C.exchange, C.quoteAsset, C.effectiveTopN,
                        JPAExpressions.selectOne().from(E).where(enrollmentMatch()).exists()))
                .from(C).where(filter(scope, search)).orderBy(primary, C.id.desc())
                .offset((long) search.page() * search.size()).limit(search.size()).fetch();
    }
    public Optional<ResearchCandidate> candidate(Scope scope, long id) {
        return Optional.ofNullable(query.selectFrom(C).where(candidateScope(scope), C.id.eq(id)).fetchOne());
    }
    public Optional<StoredEnrollment> enrollment(Scope scope, long candidateId) {
        return Optional.ofNullable(query.select(Projections.constructor(StoredEnrollment.class,
                        E.id, E.shadowEnrolledAt, E.gateStatus, E.gateEvaluatedAt))
                .from(C).join(E).on(enrollmentMatch()).where(candidateScope(scope), C.id.eq(candidateId))
                .fetchOne());
    }
    private com.querydsl.core.types.ConstructorExpression<SnapshotItem> snapshotProjection() {
        return Projections.constructor(SnapshotItem.class, S.id, S.capturedAt, S.createdAt,
                S.policySignature, S.researchCandidateCount, S.prefilterCandidateCount,
                S.rankedCandidateCount, S.finalCandidateCount);
    }
    public List<SnapshotItem> snapshots(Scope scope, int page, int size) {
        return query.select(snapshotProjection()).from(S).where(snapshotScope(scope))
                .orderBy(S.capturedAt.desc(), S.id.desc()).offset((long) page * size).limit(size).fetch();
    }
    public List<AutomationState> automationStates(Scope scope) {
        return query.selectFrom(A).where(A.id.userId.eq(scope.tradingUserId()),
                        A.id.exchange.eq(scope.exchange()), A.id.quoteAsset.eq(scope.quoteAsset()))
                .orderBy(A.id.datasetSchemaVersion.asc(), A.id.baselinePolicySignature.asc(),
                        A.id.effectiveTopN.asc()).fetch();
    }
}
