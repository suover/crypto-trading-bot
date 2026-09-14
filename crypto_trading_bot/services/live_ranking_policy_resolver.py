from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.analysis.market_ranking import MarketRankingPolicy
from crypto_trading_bot.config.settings import Settings, get_settings
from crypto_trading_bot.db.models import (
    AnalysisRun,
    LivePolicyCanaryActivation,
    LivePolicyCanaryRun,
    LivePolicyCanarySafetyBinding,
    LivePolicyCanaryTerminationEvent,
    User,
)
from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock
from crypto_trading_bot.services.live_policy_canary_service import (
    BASELINE_EXHAUSTED,
    BASELINE_EXPIRED,
    BASELINE_INVALID_CANARY,
    BASELINE_NO_CANARY,
    CANARY,
    RUN_SCHEMA_VERSION,
    LivePolicyCanaryError,
    baseline_ranking_policy,
    canary_context_lock_key,
    canary_run_count,
    canary_run_signature,
    load_and_validate_canary_safety_binding,
    validate_stored_canary_activation,
)
from crypto_trading_bot.services.live_policy_canary_termination_service import (
    load_and_validate_canary_termination,
)


logger = logging.getLogger(__name__)
BASELINE_STOPPED = "BASELINE_STOPPED"


class CanaryRuntimeBusyError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveRankingPolicyResolution:
    mode: str
    ranking_policy: MarketRankingPolicy
    baseline_policy_signature: str
    canary_policy_signature: str | None
    activation: LivePolicyCanaryActivation | None
    safety_binding: LivePolicyCanarySafetyBinding | None
    termination_event: LivePolicyCanaryTerminationEvent | None
    promotion_approval: object | None
    used_analysis_run_count: int
    max_analysis_runs: int | None
    fallback_reason: str | None
    canary_policy_selected: bool
    active_canary_count: int


class LiveRankingPolicyLease:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        resolution: LiveRankingPolicyResolution,
        *,
        lock: PostgresAdvisoryLock | None,
        now_fn: Callable[[], datetime],
    ) -> None:
        self.session = session
        self.settings = settings
        self.resolution = resolution
        self.lock = lock
        self.now_fn = now_fn
        self.reserved_run: LivePolicyCanaryRun | None = None

    @property
    def ranking_policy(self):
        return self.resolution.ranking_policy

    def reserve_run(self, analysis_run: AnalysisRun) -> LivePolicyCanaryRun | None:
        if self.resolution.mode != CANARY:
            return None
        if self.reserved_run is not None:
            raise LivePolicyCanaryError("Canary lease already reserved a run")
        if self.lock is None or not self.lock.acquired:
            raise LivePolicyCanaryError("Canary runtime lock is not held")
        activation = self.resolution.activation
        if activation is None:
            raise LivePolicyCanaryError("Canary activation is unavailable")
        validated, _, _ = validate_stored_canary_activation(
            self.session, activation, self.settings
        )
        binding = load_and_validate_canary_safety_binding(self.session, activation)
        if load_and_validate_canary_termination(self.session, activation, binding):
            raise LivePolicyCanaryError("Canary was manually stopped")
        now = self.now_fn().astimezone(UTC)
        used = canary_run_count(self.session, activation.id)
        if now < activation.started_at.astimezone(UTC):
            raise LivePolicyCanaryError("Canary activation has not started")
        if now >= activation.expires_at.astimezone(UTC):
            raise LivePolicyCanaryError("Canary expired before run reservation")
        if used >= activation.max_analysis_runs:
            raise LivePolicyCanaryError("Canary run limit was exhausted")
        if (
            analysis_run.user_id != activation.user_id
            or analysis_run.run_type != "MARKET_UNIVERSE"
            or not analysis_run.pipeline_run_id
        ):
            raise LivePolicyCanaryError("AnalysisRun does not match Canary context")
        run = LivePolicyCanaryRun(
            run_schema_version=RUN_SCHEMA_VERSION,
            canary_activation_id=activation.id,
            activation_signature=activation.activation_signature,
            promotion_approval_id=activation.promotion_approval_id,
            promotion_approval_signature=activation.promotion_approval_signature,
            candidate_id=activation.candidate_id,
            user_id=activation.user_id,
            exchange=activation.exchange,
            quote_asset=activation.quote_asset,
            analysis_run_id=analysis_run.id,
            pipeline_run_id=analysis_run.pipeline_run_id,
            run_ordinal=used + 1,
            baseline_policy_signature=activation.baseline_policy_signature,
            canary_policy_signature=activation.canary_policy_signature,
            used_canary_policy=True,
            reserved_at=now,
            run_signature="",
        )
        run.run_signature = canary_run_signature(run)
        self.session.add(run)
        self.session.flush()
        self.session.commit()
        self.reserved_run = run
        return run

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.lock is not None:
            self.lock.release()


class LiveRankingPolicyResolver:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        now_fn: Callable[[], datetime] | None = None,
        lock_factory: Callable[[int], PostgresAdvisoryLock] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.lock_factory = lock_factory or PostgresAdvisoryLock

    def resolve(self, *, user_name: str = "Minsu") -> LiveRankingPolicyLease:
        user = self._user(user_name)
        now = self._now()
        resolution = self._resolve_without_lock(user, now)
        if resolution.mode != CANARY:
            return LiveRankingPolicyLease(
                self.session,
                self.settings,
                resolution,
                lock=None,
                now_fn=self._now,
            )
        lock = self.lock_factory(
            canary_context_lock_key(
                user.id,
                self.settings.market_universe_exchange,
                self.settings.market_universe_quote_asset,
            )
        )
        if not lock.acquire():
            raise CanaryRuntimeBusyError("CANARY_RUNTIME_BUSY")
        try:
            current = self._resolve_without_lock(user, self._now())
            if current.mode != CANARY:
                lock.release()
                return LiveRankingPolicyLease(
                    self.session,
                    self.settings,
                    current,
                    lock=None,
                    now_fn=self._now,
                )
            return LiveRankingPolicyLease(
                self.session,
                self.settings,
                current,
                lock=lock,
                now_fn=self._now,
            )
        except Exception:
            lock.release()
            raise

    def inspect(self, *, user_name: str = "Minsu") -> LiveRankingPolicyResolution:
        return self._resolve_without_lock(self._user(user_name), self._now())

    def _resolve_without_lock(self, user: User, now: datetime):
        baseline_policy, baseline_signature = baseline_ranking_policy(self.settings)
        if self.settings.market_universe_mode != "DYNAMIC":
            return self._baseline(
                BASELINE_NO_CANARY,
                baseline_policy,
                baseline_signature,
                reason="MARKET_UNIVERSE_MODE is not DYNAMIC",
            )
        exchange = self.settings.market_universe_exchange.strip().upper()
        quote_asset = self.settings.market_universe_quote_asset.strip().upper()
        rows = self.session.scalars(
            select(LivePolicyCanaryActivation)
            .where(
                LivePolicyCanaryActivation.user_id == user.id,
                LivePolicyCanaryActivation.exchange == exchange,
                LivePolicyCanaryActivation.quote_asset == quote_asset,
            )
            .order_by(LivePolicyCanaryActivation.started_at.desc())
            .execution_options(autoflush=False)
        ).all()
        if not rows:
            return self._baseline(
                BASELINE_NO_CANARY, baseline_policy, baseline_signature
            )
        evaluated = []
        try:
            for row in rows:
                validated, _, candidate_policy = validate_stored_canary_activation(
                    self.session, row, self.settings
                )
                binding = load_and_validate_canary_safety_binding(self.session, row)
                termination = load_and_validate_canary_termination(
                    self.session, row, binding
                )
                used = canary_run_count(self.session, row.id)
                evaluated.append(
                    (row, validated.row, candidate_policy, binding, termination, used)
                )
        except (ValueError, TypeError, AttributeError, KeyError) as error:
            logger.warning(
                "Canary metadata is invalid; using baseline. reason=%s", error
            )
            return self._baseline(
                BASELINE_INVALID_CANARY,
                baseline_policy,
                baseline_signature,
                reason=str(error),
            )
        active = [
            item
            for item in evaluated
            if item[0].started_at.astimezone(UTC) <= now
            and now < item[0].expires_at.astimezone(UTC)
            and item[5] < item[0].max_analysis_runs
            and item[4] is None
        ]
        if len(active) > 1:
            logger.warning("Multiple active Canary rows found; using baseline")
            return self._baseline(
                BASELINE_INVALID_CANARY,
                baseline_policy,
                baseline_signature,
                reason="MULTIPLE_ACTIVE_CANARIES",
                active_count=len(active),
            )
        if any(item[0].started_at.astimezone(UTC) > now for item in evaluated):
            logger.warning("Future-dated Canary activation found; using baseline")
            return self._baseline(
                BASELINE_INVALID_CANARY,
                baseline_policy,
                baseline_signature,
                reason="CANARY_NOT_STARTED",
            )
        stopped = [item for item in evaluated if item[4] is not None]
        if stopped:
            latest = stopped[0]
            return self._baseline(
                BASELINE_STOPPED,
                baseline_policy,
                baseline_signature,
                activation=latest[0],
                approval=latest[1],
                binding=latest[3],
                termination=latest[4],
                used=latest[5],
                reason="CANARY_MANUALLY_STOPPED",
            )
        if len(active) == 1:
            activation, approval, candidate_policy, binding, _, used = active[0]
            return LiveRankingPolicyResolution(
                mode=CANARY,
                ranking_policy=candidate_policy,
                baseline_policy_signature=baseline_signature,
                canary_policy_signature=activation.canary_policy_signature,
                activation=activation,
                safety_binding=binding,
                termination_event=None,
                promotion_approval=approval,
                used_analysis_run_count=used,
                max_analysis_runs=activation.max_analysis_runs,
                fallback_reason=None,
                canary_policy_selected=True,
                active_canary_count=1,
            )
        unexpired = [
            item for item in evaluated if now < item[0].expires_at.astimezone(UTC)
        ]
        if any(item[5] >= item[0].max_analysis_runs for item in unexpired):
            latest = unexpired[0]
            return self._baseline(
                BASELINE_EXHAUSTED,
                baseline_policy,
                baseline_signature,
                activation=latest[0],
                approval=latest[1],
                binding=latest[3],
                used=latest[5],
                reason="MAX_ANALYSIS_RUNS_REACHED",
            )
        latest = evaluated[0]
        return self._baseline(
            BASELINE_EXPIRED,
            baseline_policy,
            baseline_signature,
            activation=latest[0],
            approval=latest[1],
            binding=latest[3],
            used=latest[5],
            reason="CANARY_EXPIRED",
        )

    @staticmethod
    def _baseline(
        mode,
        policy,
        signature,
        *,
        activation=None,
        approval=None,
        binding=None,
        termination=None,
        used=0,
        reason=None,
        active_count=0,
    ):
        return LiveRankingPolicyResolution(
            mode=mode,
            ranking_policy=policy,
            baseline_policy_signature=signature,
            canary_policy_signature=(
                activation.canary_policy_signature if activation is not None else None
            ),
            activation=activation,
            safety_binding=binding,
            termination_event=termination,
            promotion_approval=approval,
            used_analysis_run_count=used,
            max_analysis_runs=(
                activation.max_analysis_runs if activation is not None else None
            ),
            fallback_reason=reason,
            canary_policy_selected=False,
            active_canary_count=active_count,
        )

    def _user(self, user_name):
        user = self.session.scalar(
            select(User)
            .where(User.name == user_name)
            .execution_options(autoflush=False)
        )
        if user is None:
            raise ValueError(f"User not found. name={user_name}")
        return user

    def _now(self):
        value = self.now_fn()
        if value.tzinfo is None or value.utcoffset() is None:
            raise LivePolicyCanaryError("Canary runtime clock must be timezone-aware")
        return value.astimezone(UTC)


__all__ = [
    "CanaryRuntimeBusyError",
    "BASELINE_STOPPED",
    "LiveRankingPolicyLease",
    "LiveRankingPolicyResolution",
    "LiveRankingPolicyResolver",
]
