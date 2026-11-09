"""Scope, validate and enqueue review notifications without sending them."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import (
    ShadowPolicyEnrollment,
    ShadowPolicyPromotionApproval,
    ShadowReviewMonitorState,
    ShadowReviewNotification,
)
from crypto_trading_bot.services.live_ranking_policy_resolver import (
    FULL_LIVE,
    LiveRankingPolicyResolver,
)
from crypto_trading_bot.services.runtime_user_resolver import RuntimeUserResolver
from crypto_trading_bot.services.shadow_policy_promotion_approval_service import (
    validate_shadow_review,
)
from crypto_trading_bot.services.shadow_policy_provenance import (
    load_and_validate_shadow_policy_enrollment,
)
from crypto_trading_bot.services.shadow_review_gate_service import (
    ELIGIBLE_FOR_PROMOTION_REVIEW,
    INVALID_REVIEW_DATA,
    SHADOW_REVIEW_GATE_V1,
    ShadowReviewGateResult,
    ShadowReviewGateService,
    shadow_review_policy_signature,
)


ALREADY_APPROVED = "ALREADY_APPROVED"
CONTEXT_BLOCKED = "CONTEXT_BLOCKED"
IDENTITY_FIELDS = (
    "candidate_id",
    "user_id",
    "exchange",
    "quote_asset",
    "scenario_definition_signature",
    "baseline_policy_signature",
    "effective_top_n",
)


@dataclass
class ShadowReviewMonitorResult:
    monitoring_enabled: bool
    telegram_notifications_enabled: bool
    apply: bool
    report_type: str = "SHADOW_REVIEW_MONITOR_CYCLE"
    status: str = "SUCCESS"
    safe_reason: str | None = None
    inspected_enrollment_count: int = 0
    eligible_candidate_count: int = 0
    insufficient_candidate_count: int = 0
    not_eligible_candidate_count: int = 0
    invalid_candidate_count: int = 0
    already_approved_candidate_count: int = 0
    no_shadow_enrollment_count: int = 0
    context_blocked_candidate_count: int = 0
    execution_error_count: int = 0
    existing_notification_count: int = 0
    created_notification_count: int = 0
    deferred_notification_count: int = 0
    pending_notification_count: int = 0
    sending_notification_count: int = 0
    sent_notification_count: int = 0
    failed_notification_count: int = 0
    uncertain_notification_count: int = 0
    suppressed_notification_count: int = 0
    cursor_enrollment_id: int = 0
    sweep_enrollment_id_ceiling: int = 0
    database_write: bool = False
    external_calls: bool = False
    telegram_messages_sent: int = 0
    human_approval_created: bool = False
    live_policy_change: bool = False
    live_orders_created: int = 0

    @classmethod
    def from_settings(cls, settings: Settings, *, apply: bool):
        return cls(
            settings.shadow_review_monitor_enabled,
            settings.shadow_review_telegram_notifications_enabled,
            apply,
        )


@dataclass(frozen=True)
class ShadowReviewContext:
    user_id: int
    exchange: str
    quote_asset: str

    @classmethod
    def load(cls, session: Session, settings: Settings):
        return cls(
            RuntimeUserResolver(session)
            .resolve_configured(settings.trading_user_id)
            .id,
            settings.market_universe_exchange.strip().upper(),
            settings.market_universe_quote_asset.strip().upper(),
        )

    @property
    def key(self) -> tuple[int, str, str]:
        return self.user_id, self.exchange, self.quote_asset

    def predicates(self, model) -> tuple:
        return (
            model.user_id == self.user_id,
            model.exchange == self.exchange,
            model.quote_asset == self.quote_asset,
        )


def approval_exists(session: Session, candidate_id: int) -> bool:
    return (
        session.scalar(
            select(ShadowPolicyPromotionApproval.id).where(
                ShadowPolicyPromotionApproval.candidate_id == candidate_id
            )
        )
        is not None
    )


def same_enrollment_identity(left, right) -> bool:
    return all(
        getattr(left, field) == getattr(right, field) for field in IDENTITY_FIELDS
    )


class ShadowReviewEligibility:
    def __init__(self, settings: Settings, *, gate_factory=ShadowReviewGateService):
        self.settings = settings
        self.gate_factory = gate_factory

    def evaluate(
        self,
        session: Session,
        enrollment: ShadowPolicyEnrollment,
        context: ShadowReviewContext,
    ) -> tuple[str, ShadowReviewGateResult | None]:
        if (
            enrollment.user_id,
            enrollment.exchange,
            enrollment.quote_asset,
        ) != context.key:
            return CONTEXT_BLOCKED, None
        if approval_exists(session, enrollment.candidate_id):
            return ALREADY_APPROVED, None
        if enrollment.effective_top_n != self.settings.market_universe_top_n:
            return CONTEXT_BLOCKED, None

        review = self.gate_factory(session).evaluate(
            candidate_id=enrollment.candidate_id
        )
        status = validate_shadow_review(review, enrollment.candidate_id)
        if status != ELIGIBLE_FOR_PROMOTION_REVIEW:
            return status, None

        verified = load_and_validate_shadow_policy_enrollment(
            session, enrollment.candidate_id
        )
        evidence = review.performance
        if (
            verified is None
            or verified.row.id != enrollment.id
            or review.enrollment.id != enrollment.id
            or not same_enrollment_identity(enrollment, verified.row)
            or not same_enrollment_identity(enrollment, review.enrollment)
            or not same_enrollment_identity(enrollment, evidence.enrollment)
            or review.enrollment.scenario_name != enrollment.scenario_name
            or evidence.enrollment.scenario_name != enrollment.scenario_name
            or review.enrollment.gate_decision_signature
            != enrollment.gate_decision_signature
            or evidence.enrollment.gate_decision_signature
            != enrollment.gate_decision_signature
            or not isinstance(evidence.performance_evidence_as_of, datetime)
            or evidence.performance_evidence_as_of.utcoffset() is None
            or not enrollment.shadow_enrolled_at
            <= evidence.performance_evidence_as_of
            <= datetime.now(UTC)
            or type(evidence.shadow_evaluation_snapshot_id_ceiling) is not int
            or evidence.shadow_evaluation_snapshot_id_ceiling
            <= enrollment.shadow_snapshot_id_watermark
        ):
            return INVALID_REVIEW_DATA, None

        runtime = LiveRankingPolicyResolver(session, settings=self.settings).inspect(
            user_id=context.user_id,
            exchange=context.exchange,
            quote_asset=context.quote_asset,
        )
        if (
            runtime.baseline_policy_signature != enrollment.baseline_policy_signature
            or runtime.mode == FULL_LIVE
        ):
            return CONTEXT_BLOCKED, None
        if approval_exists(session, enrollment.candidate_id):
            return ALREADY_APPROVED, None
        return status, review


class ShadowReviewMonitorService:
    def __init__(self, session_factory, *, settings: Settings, eligibility=None):
        self.session_factory = session_factory
        self.settings = settings
        self.eligibility = eligibility or ShadowReviewEligibility(settings)

    def run_cycle(self, *, apply: bool = False) -> ShadowReviewMonitorResult:
        result = ShadowReviewMonitorResult.from_settings(self.settings, apply=apply)
        if apply and not result.monitoring_enabled:
            result.status = "APPLY_DISABLED"
            return result

        with self.session_factory() as session:
            if not apply:
                session.execute(text("SET TRANSACTION READ ONLY"))
            context = ShadowReviewContext.load(session, self.settings)
            destination = self.settings.shadow_review_destination
            ids, cursor, ceiling = self._batch(session, context)
            result.cursor_enrollment_id = cursor
            result.sweep_enrollment_id_ceiling = ceiling
            for enrollment_id in ids:
                enrollment = session.get(ShadowPolicyEnrollment, enrollment_id)
                if enrollment is None:
                    continue
                result.inspected_enrollment_count += 1
                self._inspect(session, enrollment, context, destination, result)

            if apply:
                state = session.get(ShadowReviewMonitorState, context.key)
                if (state is None and ids) or (
                    state is not None
                    and (
                        state.cursor_enrollment_id != cursor
                        or state.sweep_enrollment_id_ceiling != ceiling
                    )
                ):
                    self._advance_cursor(session, context, cursor, ceiling)
                    result.database_write = True

            read_outbox_counts(session, context, result)
            if result.database_write:
                session.commit()
            if result.execution_error_count or result.invalid_candidate_count:
                result.status = "PARTIAL"
                result.safe_reason = "candidate validation or evaluation failed"
            elif apply and destination is None:
                result.safe_reason = "Telegram destination not configured"
        return result

    @staticmethod
    def _advance_cursor(session, context, cursor, ceiling) -> None:
        state = ShadowReviewMonitorState
        position = {
            "cursor_enrollment_id": cursor,
            "sweep_enrollment_id_ceiling": ceiling,
        }
        session.execute(
            insert(state)
            .values(
                user_id=context.user_id,
                exchange=context.exchange,
                quote_asset=context.quote_asset,
                **position,
            )
            .on_conflict_do_update(
                index_elements=[state.user_id, state.exchange, state.quote_asset],
                set_=position,
            )
        )

    def _batch(self, session, context):
        state = session.get(ShadowReviewMonitorState, context.key)
        cursor = state.cursor_enrollment_id if state else 0
        ceiling = state.sweep_enrollment_id_ceiling if state else 0
        enrollment = ShadowPolicyEnrollment
        scope = context.predicates(enrollment)
        if cursor >= ceiling:
            cursor = 0
            ceiling = session.scalar(select(func.max(enrollment.id)).where(*scope)) or 0
        ids = tuple(
            session.scalars(
                select(enrollment.id)
                .where(*scope, enrollment.id > cursor, enrollment.id <= ceiling)
                .order_by(enrollment.id)
                .limit(self.settings.shadow_review_monitor_batch_size)
            )
        )
        # A deleted tail must not pin the cursor. The next cycle starts a fresh sweep.
        return ids, ids[-1] if ids else ceiling, ceiling

    def _inspect(self, session, enrollment, context, destination, result):
        if (
            destination is not None
            and session.scalar(
                select(ShadowReviewNotification.id).where(
                    ShadowReviewNotification.shadow_enrollment_id == enrollment.id,
                    ShadowReviewNotification.review_policy_signature
                    == shadow_review_policy_signature(SHADOW_REVIEW_GATE_V1),
                    ShadowReviewNotification.notification_destination == destination,
                )
            )
            is not None
        ):
            result.existing_notification_count += 1
            return
        try:
            status, review = self.eligibility.evaluate(session, enrollment, context)
        except SQLAlchemyError:
            raise
        except ValueError, TypeError, KeyError, AttributeError:
            status, review = INVALID_REVIEW_DATA, None
        except Exception:
            result.execution_error_count += 1
            return

        if review is None:
            field = {
                ALREADY_APPROVED: "already_approved_candidate_count",
                CONTEXT_BLOCKED: "context_blocked_candidate_count",
                "NO_SHADOW_ENROLLMENT": "no_shadow_enrollment_count",
                "INSUFFICIENT_DATA": "insufficient_candidate_count",
                "NOT_ELIGIBLE": "not_eligible_candidate_count",
                INVALID_REVIEW_DATA: "invalid_candidate_count",
            }[status]
            setattr(result, field, getattr(result, field) + 1)
            return

        result.eligible_candidate_count += 1
        if not result.apply:
            return
        if (
            destination is None
            or result.created_notification_count
            >= self.settings.shadow_review_notification_max_per_cycle
        ):
            result.deferred_notification_count += 1
            return

        row = ShadowReviewNotification
        notification_id = session.scalar(
            insert(row)
            .values(
                **{field: getattr(enrollment, field) for field in IDENTITY_FIELDS},
                shadow_enrollment_id=enrollment.id,
                review_policy_schema_version=review.review_policy_schema_version,
                review_policy_signature=review.review_policy_signature,
                initial_review_decision_signature=review.review_decision_signature,
                first_eligible_at=review.evaluated_at,
                shadow_evaluation_snapshot_id_ceiling=review.performance.shadow_evaluation_snapshot_id_ceiling,
                notification_destination=destination,
                delivery_status="PENDING",
            )
            .on_conflict_do_nothing(
                constraint="uq_shadow_review_notification_destination"
            )
            .returning(row.id)
        )
        if notification_id is None:
            result.existing_notification_count += 1
        else:
            result.created_notification_count += 1
            result.database_write = True


def read_outbox_counts(
    session: Session, context: ShadowReviewContext, result: ShadowReviewMonitorResult
) -> None:
    row = ShadowReviewNotification
    counts = dict(
        session.execute(
            select(row.delivery_status, func.count())
            .where(*context.predicates(row))
            .group_by(row.delivery_status)
        ).all()
    )
    for status in ("pending", "sending", "sent", "failed", "uncertain", "suppressed"):
        setattr(result, f"{status}_notification_count", counts.get(status.upper(), 0))
