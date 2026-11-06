from collections import Counter
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import ResearchPolicyCandidate, ShadowPolicyEnrollment
from crypto_trading_bot.services.policy_promotion_gate_service import (
    ELIGIBLE_FOR_REVIEW,
    INSUFFICIENT_DATA,
    INVALID_PROMOTION_DATA,
    NOT_ELIGIBLE,
    PolicyPromotionGateService,
)
from crypto_trading_bot.services.shadow_policy_enrollment_service import (
    ALREADY_ENROLLED,
    CREATED,
    DRY_RUN,
    GATE_NOT_ELIGIBLE,
    INVALID_SHADOW_ENROLLMENT,
    ShadowPolicyEnrollmentService,
)


EXECUTION_ERROR = "EXECUTION_ERROR"
GATE_STATUSES = {
    ELIGIBLE_FOR_REVIEW,
    INSUFFICIENT_DATA,
    INVALID_PROMOTION_DATA,
    NOT_ELIGIBLE,
}


@dataclass(frozen=True)
class ResearchLifecycleCandidateResult:
    candidate_id: int
    status: str
    gate_status: str | None = None
    enrollment_status: str | None = None
    database_write: bool = False
    error_type: str | None = None


@dataclass(frozen=True)
class ResearchLifecycleCycleResult:
    registered_candidate_count: int
    candidates: tuple[ResearchLifecycleCandidateResult, ...]
    cursor_candidate_id: int
    sweep_candidate_id_ceiling: int
    apply: bool
    live_policy_change: bool = False
    live_orders_created: bool = False
    external_calls: bool = False

    @property
    def database_write(self) -> bool:
        return any(candidate.database_write for candidate in self.candidates)

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(candidate.status for candidate in self.candidates))


class ResearchLifecycleService:
    """Coordinate existing evaluators; only CREATED enrollments are committed.

    A bounded ID sweep prevents a growing registry from starving older candidates.
    The cursor is process-local: restarting safely restarts validation at the first ID.
    No evaluation history or alternate promotion decision is persisted here.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        settings: Settings,
        gate_service_factory=PolicyPromotionGateService,
        enrollment_service_factory=ShadowPolicyEnrollmentService,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.gate_service_factory = gate_service_factory
        self.enrollment_service_factory = enrollment_service_factory
        self.cursor_candidate_id = 0
        self.sweep_candidate_id_ceiling = 0

    def run_cycle(self, *, apply: bool = False) -> ResearchLifecycleCycleResult:
        if apply and not (
            self.settings.research_lifecycle_enabled
            and self.settings.research_auto_shadow_enrollment_enabled
        ):
            raise ValueError("Research lifecycle Apply requires both enable flags")

        # Enumeration ends before any candidate transaction starts. Freeze the
        # sweep ceiling until all its IDs have been visited, including failures.
        with self.session_factory() as session:
            total = session.scalar(
                select(func.count()).select_from(ResearchPolicyCandidate)
            )
            if self.cursor_candidate_id >= self.sweep_candidate_id_ceiling:
                self.cursor_candidate_id = 0
                self.sweep_candidate_id_ceiling = (
                    session.scalar(select(func.max(ResearchPolicyCandidate.id))) or 0
                )
            candidate_ids = tuple(
                session.scalars(
                    select(ResearchPolicyCandidate.id)
                    .where(
                        ResearchPolicyCandidate.id > self.cursor_candidate_id,
                        ResearchPolicyCandidate.id <= self.sweep_candidate_id_ceiling,
                    )
                    .order_by(ResearchPolicyCandidate.id)
                    .limit(self.settings.research_lifecycle_batch_size)
                    .execution_options(autoflush=False)
                )
            )
            session.rollback()

        results = []
        for candidate_id in candidate_ids:
            results.append(self._process_candidate(candidate_id, apply=apply))
            self.cursor_candidate_id = candidate_id
        if not candidate_ids:
            # Deleted candidates cannot leave a sweep stuck before its ceiling.
            self.cursor_candidate_id = self.sweep_candidate_id_ceiling
        return ResearchLifecycleCycleResult(
            registered_candidate_count=total,
            candidates=tuple(results),
            cursor_candidate_id=self.cursor_candidate_id,
            sweep_candidate_id_ceiling=self.sweep_candidate_id_ceiling,
            apply=apply,
        )

    def _process_candidate(
        self, candidate_id: int, *, apply: bool
    ) -> ResearchLifecycleCandidateResult:
        try:
            with self.session_factory() as session:
                try:
                    result = self._evaluate(session, candidate_id, apply=apply)
                    if result.database_write:
                        session.commit()
                    else:
                        session.rollback()
                    return result
                except BaseException:
                    session.rollback()
                    raise
        except Exception as error:
            # DB exception messages may contain credentials/parameters. Report
            # only the exception class; never serialize exceptions or Gate text.
            return ResearchLifecycleCandidateResult(
                candidate_id, EXECUTION_ERROR, error_type=type(error).__name__
            )

    def _evaluate(
        self, session: Session, candidate_id: int, *, apply: bool
    ) -> ResearchLifecycleCandidateResult:
        enrollment_service = self.enrollment_service_factory(session)
        existing_id = session.scalar(
            select(ShadowPolicyEnrollment.id)
            .where(ShadowPolicyEnrollment.candidate_id == candidate_id)
            .execution_options(autoflush=False)
        )
        if existing_id is not None:
            # preview validates stored provenance without fresh Gate evaluation.
            enrollment = enrollment_service.preview(candidate_id=candidate_id)
            return self._enrollment_result(candidate_id, enrollment, apply=False)

        gate = self.gate_service_factory(session).evaluate(candidate_id=candidate_id)
        if gate.candidate_id != candidate_id or gate.status not in GATE_STATUSES:
            raise RuntimeError("Unexpected promotion Gate identity or status")
        if gate.status != ELIGIBLE_FOR_REVIEW:
            return ResearchLifecycleCandidateResult(
                candidate_id, gate.status, gate_status=gate.status
            )

        # Do not pass the earlier Gate result: enrollment must reevaluate with
        # its own current evidence, signature and authoritative watermark.
        enrollment = (
            enrollment_service.enroll(candidate_id=candidate_id)
            if apply
            else enrollment_service.preview(candidate_id=candidate_id)
        )
        return self._enrollment_result(candidate_id, enrollment, apply=apply)

    @staticmethod
    def _enrollment_result(candidate_id, enrollment, *, apply):
        status = enrollment.enrollment_status
        if enrollment.candidate_id != candidate_id or status not in {
            CREATED,
            ALREADY_ENROLLED,
            DRY_RUN,
            GATE_NOT_ELIGIBLE,
            INVALID_SHADOW_ENROLLMENT,
        }:
            raise RuntimeError("Unexpected Shadow enrollment identity or status")
        created = status == CREATED
        if (
            enrollment.external_calls
            or enrollment.live_policy_change
            or enrollment.promotion_performed
            or enrollment.database_write != created
            or enrollment.shadow_enrollment_created != created
            or (created and (not apply or enrollment.enrollment is None))
        ):
            raise RuntimeError("Inconsistent Shadow enrollment result")
        gate_status = enrollment.gate.status if enrollment.gate is not None else None
        if status == GATE_NOT_ELIGIBLE:
            if gate_status not in (INSUFFICIENT_DATA, NOT_ELIGIBLE):
                raise RuntimeError("Unexpected changed Gate status")
            status = gate_status
        return ResearchLifecycleCandidateResult(
            candidate_id,
            status,
            gate_status=gate_status,
            enrollment_status=enrollment.enrollment_status,
            database_write=created,
        )
