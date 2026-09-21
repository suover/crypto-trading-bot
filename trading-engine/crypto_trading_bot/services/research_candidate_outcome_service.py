from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    StrategyReplayCandidate,
    StrategyReplayCandidateOutcome,
    StrategyReplaySnapshot,
)
from crypto_trading_bot.services.historical_outcome_price_resolver import (
    HistoricalOutcomePriceResolver,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
)


REFERENCE_PRICE_SOURCE = "STRATEGY_REPLAY_CANDIDATE_FEATURE_LATEST_PRICE"
END_PRICE_SOURCE = "UPBIT_MINUTE_CANDLE_1M_CLOSE"
UNAVAILABLE_SOURCE = "UNAVAILABLE"
RETRYABLE_SAFE_REASONS = frozenset({"HISTORICAL_PRICE_UNAVAILABLE"})


@dataclass(frozen=True)
class ResearchCandidateOutcomeCycleResult:
    candidate_count: int
    due_outcome_count: int
    complete_count: int
    partial_count: int
    new_count: int
    update_count: int
    skipped_complete_count: int
    skipped_non_retryable_count: int
    by_horizon: dict[int, dict[str, int]]


class ResearchCandidateOutcomeService:
    """Evaluate frozen ranking candidates against closed historical candles."""

    def __init__(
        self,
        session: Session,
        *,
        price_resolver: HistoricalOutcomePriceResolver | None = None,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session = session
        self.price_resolver = price_resolver or HistoricalOutcomePriceResolver()
        self.now_fn = now_fn

    def evaluate_due(
        self,
        *,
        horizons: Iterable[int],
        batch_size: int,
        user_id: int | None = None,
        snapshot_id: int | None = None,
        apply: bool = False,
    ) -> ResearchCandidateOutcomeCycleResult:
        normalized_horizons = tuple(sorted(set(horizons)))
        if not normalized_horizons or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in normalized_horizons
        ):
            raise ValueError("horizons must contain positive integers")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        now = HistoricalOutcomePriceResolver.aware_utc(self.now_fn())
        if now is None:
            raise ValueError("now must be timezone-aware")
        due_conditions = []
        for horizon in normalized_horizons:
            any_outcome = exists().where(
                StrategyReplayCandidateOutcome.strategy_replay_candidate_id
                == StrategyReplayCandidate.id,
                StrategyReplayCandidateOutcome.horizon_minutes == horizon,
            )
            retryable = exists().where(
                StrategyReplayCandidateOutcome.strategy_replay_candidate_id
                == StrategyReplayCandidate.id,
                StrategyReplayCandidateOutcome.horizon_minutes == horizon,
                StrategyReplayCandidateOutcome.evaluation_status == "PARTIAL",
                StrategyReplayCandidateOutcome.safe_reason
                == "HISTORICAL_PRICE_UNAVAILABLE",
            )
            due_conditions.append(
                and_(
                    StrategyReplaySnapshot.captured_at
                    <= now - timedelta(minutes=horizon),
                    or_(~any_outcome, retryable),
                )
            )
        last_attempt = (
            select(func.max(StrategyReplayCandidateOutcome.evaluated_at))
            .where(
                StrategyReplayCandidateOutcome.strategy_replay_candidate_id
                == StrategyReplayCandidate.id
            )
            .correlate(StrategyReplayCandidate)
            .scalar_subquery()
        )
        query = (
            select(StrategyReplayCandidate, StrategyReplaySnapshot)
            .join(
                StrategyReplaySnapshot,
                StrategyReplaySnapshot.id
                == StrategyReplayCandidate.strategy_replay_snapshot_id,
            )
            .where(
                StrategyReplayCandidate.in_prefilter.is_(True),
                StrategyReplayCandidate.buy_eligible.is_(True),
                StrategyReplayCandidate.feature_data["enough_candles"]
                .as_boolean()
                .is_(True),
                or_(*due_conditions),
            )
            .order_by(
                last_attempt.asc().nulls_first(),
                StrategyReplaySnapshot.captured_at.asc(),
                StrategyReplayCandidate.id.asc(),
            )
            .limit(batch_size)
            .execution_options(autoflush=False)
        )
        if user_id is not None:
            query = query.where(StrategyReplayCandidate.user_id == user_id)
        if snapshot_id is not None:
            query = query.where(StrategyReplaySnapshot.id == snapshot_id)
        candidates = tuple(self.session.execute(query))

        counts = {
            "due": 0,
            "complete": 0,
            "partial": 0,
            "new": 0,
            "update": 0,
            "skipped_complete": 0,
            "skipped_non_retryable": 0,
        }
        by_horizon = {
            horizon: {"complete": 0, "partial": 0} for horizon in normalized_horizons
        }
        for candidate, snapshot in candidates:
            snapshot_at = HistoricalOutcomePriceResolver.aware_utc(snapshot.captured_at)
            if snapshot_at is None:
                continue
            for horizon in normalized_horizons:
                target_at = snapshot_at + timedelta(minutes=horizon)
                if target_at > now:
                    continue
                existing = self.session.scalar(
                    select(StrategyReplayCandidateOutcome)
                    .where(
                        StrategyReplayCandidateOutcome.strategy_replay_candidate_id
                        == candidate.id,
                        StrategyReplayCandidateOutcome.horizon_minutes == horizon,
                    )
                    .execution_options(autoflush=False)
                )
                if existing is not None and existing.evaluation_status == "COMPLETE":
                    counts["skipped_complete"] += 1
                    continue
                if (
                    existing is not None
                    and existing.evaluation_status == "PARTIAL"
                    and existing.safe_reason not in RETRYABLE_SAFE_REASONS
                ):
                    counts["skipped_non_retryable"] += 1
                    continue
                values = self._values(
                    candidate=candidate,
                    snapshot=snapshot,
                    snapshot_at=snapshot_at,
                    target_at=target_at,
                    horizon=horizon,
                    now=now,
                )
                counts["due"] += 1
                status = values["evaluation_status"].lower()
                counts[status] += 1
                counts["new" if existing is None else "update"] += 1
                by_horizon[horizon][status] += 1
                if apply:
                    self._upsert(existing, values)
        if apply:
            self.session.flush()
        return ResearchCandidateOutcomeCycleResult(
            candidate_count=len(candidates),
            due_outcome_count=counts["due"],
            complete_count=counts["complete"],
            partial_count=counts["partial"],
            new_count=counts["new"],
            update_count=counts["update"],
            skipped_complete_count=counts["skipped_complete"],
            skipped_non_retryable_count=counts["skipped_non_retryable"],
            by_horizon=by_horizon,
        )

    def _values(
        self,
        *,
        candidate: StrategyReplayCandidate,
        snapshot: StrategyReplaySnapshot,
        snapshot_at: datetime,
        target_at: datetime,
        horizon: int,
        now: datetime,
    ) -> dict[str, Any]:
        base = {
            "strategy_replay_candidate_id": candidate.id,
            "strategy_replay_snapshot_id": snapshot.id,
            "user_id": candidate.user_id,
            "exchange": candidate.exchange,
            "market": candidate.market,
            "horizon_minutes": horizon,
            "snapshot_at": snapshot_at,
            "reference_at": snapshot_at,
            "target_at": target_at,
            "evaluated_at": now,
        }
        if (
            candidate.strategy_replay_snapshot_id != snapshot.id
            or candidate.user_id != snapshot.user_id
            or candidate.exchange != snapshot.exchange
        ):
            return self._partial(base, "DATASET_LINEAGE_MISMATCH")
        if snapshot.dataset_schema_version != DATASET_SCHEMA_VERSION:
            return self._partial(base, "UNSUPPORTED_DATASET_SCHEMA")
        if candidate.exchange != self.price_resolver.exchange_code:
            return self._partial(base, "UNSUPPORTED_EXCHANGE")
        reference = HistoricalOutcomePriceResolver.positive_decimal(
            (candidate.feature_data or {}).get("latest_price")
        )
        if reference is None:
            return self._partial(base, "REFERENCE_PRICE_UNAVAILABLE")
        end = self.price_resolver.resolve(
            exchange=candidate.exchange,
            market=candidate.market,
            target_at=target_at,
        )
        if end is None:
            values = self._partial(base, "HISTORICAL_PRICE_UNAVAILABLE")
            values.update(
                reference_price=reference,
                reference_price_source=REFERENCE_PRICE_SOURCE,
            )
            return values
        end_price, end_at = end
        return {
            **base,
            "reference_price": reference,
            "reference_price_source": REFERENCE_PRICE_SOURCE,
            "end_price": end_price,
            "end_price_at": end_at,
            "end_price_source": END_PRICE_SOURCE,
            "market_return_percentage": (end_price / reference - Decimal("1"))
            * Decimal("100"),
            "evaluation_status": "COMPLETE",
            "safe_reason": None,
        }

    @staticmethod
    def _partial(base: dict[str, Any], reason: str) -> dict[str, Any]:
        return {
            **base,
            "reference_price": None,
            "reference_price_source": UNAVAILABLE_SOURCE,
            "end_price": None,
            "end_price_at": None,
            "end_price_source": UNAVAILABLE_SOURCE,
            "market_return_percentage": None,
            "evaluation_status": "PARTIAL",
            "safe_reason": reason,
        }

    def _upsert(
        self,
        existing: StrategyReplayCandidateOutcome | None,
        values: dict[str, Any],
    ) -> None:
        if existing is None:
            self.session.add(StrategyReplayCandidateOutcome(**values))
            return
        for key, value in values.items():
            setattr(existing, key, value)
