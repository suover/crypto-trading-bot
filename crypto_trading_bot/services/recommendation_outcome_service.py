from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    AnalysisRun,
    MarketUniverseCandidate,
    TradeRecommendation,
    TradeRecommendationCandidateOutcome,
    TradeRecommendationOutcome,
)
from crypto_trading_bot.exchange.market_data import ExchangeMarketDataProvider
from crypto_trading_bot.exchange.upbit_market_data_provider import (
    UpbitMarketDataProvider,
)


REFERENCE_PRICE_SOURCE = "MARKET_UNIVERSE_CANDIDATE"
END_PRICE_SOURCE = "UPBIT_MINUTE_CANDLE_1M_CLOSE"
UNAVAILABLE_SOURCE = "UNAVAILABLE"
HISTORICAL_CANDLE_COUNT = 10


@dataclass(frozen=True)
class OutcomeCycleResult:
    recommendation_count: int
    due_outcome_count: int
    selected_complete_count: int
    selected_partial_count: int
    selected_new_count: int
    selected_update_count: int
    candidate_complete_count: int
    candidate_partial_count: int
    candidate_new_count: int
    candidate_update_count: int
    by_horizon: dict[int, dict[str, int]]


class RecommendationOutcomeService:
    """Evaluate frozen recommendation inputs against no-lookahead market prices."""

    def __init__(
        self,
        session: Session,
        *,
        market_data_provider: ExchangeMarketDataProvider | None = None,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session = session
        self.provider = market_data_provider or UpbitMarketDataProvider()
        self.now_fn = now_fn
        self._price_cache: dict[
            tuple[str, datetime], tuple[Decimal, datetime] | None
        ] = {}

    def evaluate_due(
        self,
        *,
        horizons: Iterable[int],
        batch_size: int,
        user_id: int | None = None,
        apply: bool = False,
    ) -> OutcomeCycleResult:
        normalized_horizons = tuple(sorted(set(horizons)))
        if not normalized_horizons or any(value <= 0 for value in normalized_horizons):
            raise ValueError("horizons must contain positive integers")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        now = self._aware_utc(self.now_fn())
        if now is None:
            raise ValueError("now must be timezone-aware")
        due_conditions = []
        for horizon in normalized_horizons:
            any_selected = exists().where(
                TradeRecommendationOutcome.recommendation_id == TradeRecommendation.id,
                TradeRecommendationOutcome.horizon_minutes == horizon,
            )
            retryable_selected = exists().where(
                TradeRecommendationOutcome.recommendation_id == TradeRecommendation.id,
                TradeRecommendationOutcome.horizon_minutes == horizon,
                TradeRecommendationOutcome.evaluation_status == "PARTIAL",
                TradeRecommendationOutcome.safe_reason
                == "HISTORICAL_PRICE_UNAVAILABLE",
            )
            retryable_candidate = exists().where(
                TradeRecommendationCandidateOutcome.recommendation_id
                == TradeRecommendation.id,
                TradeRecommendationCandidateOutcome.horizon_minutes == horizon,
                TradeRecommendationCandidateOutcome.evaluation_status == "PARTIAL",
                TradeRecommendationCandidateOutcome.safe_reason
                == "HISTORICAL_PRICE_UNAVAILABLE",
            )
            due_conditions.append(
                and_(
                    TradeRecommendation.created_at <= now - timedelta(minutes=horizon),
                    or_(~any_selected, retryable_selected, retryable_candidate),
                )
            )
        selected_last_attempt = (
            select(func.max(TradeRecommendationOutcome.evaluated_at))
            .where(
                TradeRecommendationOutcome.recommendation_id == TradeRecommendation.id
            )
            .correlate(TradeRecommendation)
            .scalar_subquery()
        )
        candidate_last_attempt = (
            select(func.max(TradeRecommendationCandidateOutcome.evaluated_at))
            .where(
                TradeRecommendationCandidateOutcome.recommendation_id
                == TradeRecommendation.id
            )
            .correlate(TradeRecommendation)
            .scalar_subquery()
        )
        last_attempt = func.coalesce(candidate_last_attempt, selected_last_attempt)
        query = (
            select(TradeRecommendation)
            .where(or_(*due_conditions))
            .order_by(
                last_attempt.asc().nulls_first(),
                TradeRecommendation.created_at.desc(),
                TradeRecommendation.id.desc(),
            )
            .limit(batch_size)
        )
        if user_id is not None:
            query = query.where(TradeRecommendation.user_id == user_id)
        recommendations = tuple(self.session.scalars(query))

        counts = {
            "due": 0,
            "selected_complete": 0,
            "selected_partial": 0,
            "selected_new": 0,
            "selected_update": 0,
            "candidate_complete": 0,
            "candidate_partial": 0,
            "candidate_new": 0,
            "candidate_update": 0,
        }
        by_horizon = {h: {"complete": 0, "partial": 0} for h in normalized_horizons}
        for recommendation in recommendations:
            recommendation_at = self._aware_utc(recommendation.created_at)
            if recommendation_at is None:
                continue
            candidates = self._exact_candidates(recommendation)
            selected_candidate = next(
                (
                    row
                    for row in candidates
                    if row.id == recommendation.universe_candidate_id
                ),
                None,
            )
            for horizon in normalized_horizons:
                target_at = recommendation_at + timedelta(minutes=horizon)
                if target_at > now:
                    continue
                counts["due"] += 1
                selected_existing = self.session.scalar(
                    select(TradeRecommendationOutcome).where(
                        TradeRecommendationOutcome.recommendation_id
                        == recommendation.id,
                        TradeRecommendationOutcome.horizon_minutes == horizon,
                    )
                )
                if (
                    selected_existing is None
                    or selected_existing.evaluation_status != "COMPLETE"
                ):
                    values = self._selected_values(
                        recommendation,
                        selected_candidate,
                        recommendation_at,
                        target_at,
                        horizon,
                        now,
                    )
                    self._record_counts(
                        counts,
                        by_horizon[horizon],
                        "selected",
                        selected_existing,
                        values,
                    )
                    if apply:
                        self._upsert(
                            selected_existing, TradeRecommendationOutcome, values
                        )
                for candidate in candidates:
                    existing = self.session.scalar(
                        select(TradeRecommendationCandidateOutcome).where(
                            TradeRecommendationCandidateOutcome.recommendation_id
                            == recommendation.id,
                            TradeRecommendationCandidateOutcome.universe_candidate_id
                            == candidate.id,
                            TradeRecommendationCandidateOutcome.horizon_minutes
                            == horizon,
                        )
                    )
                    if (
                        existing is not None
                        and existing.evaluation_status == "COMPLETE"
                    ):
                        continue
                    values = self._candidate_values(
                        recommendation,
                        candidate,
                        recommendation_at,
                        target_at,
                        horizon,
                        now,
                    )
                    self._record_counts(
                        counts, by_horizon[horizon], "candidate", existing, values
                    )
                    if apply:
                        self._upsert(
                            existing, TradeRecommendationCandidateOutcome, values
                        )
        if apply:
            self.session.flush()
        return OutcomeCycleResult(
            recommendation_count=len(recommendations),
            due_outcome_count=counts["due"],
            selected_complete_count=counts["selected_complete"],
            selected_partial_count=counts["selected_partial"],
            selected_new_count=counts["selected_new"],
            selected_update_count=counts["selected_update"],
            candidate_complete_count=counts["candidate_complete"],
            candidate_partial_count=counts["candidate_partial"],
            candidate_new_count=counts["candidate_new"],
            candidate_update_count=counts["candidate_update"],
            by_horizon=by_horizon,
        )

    def _exact_candidates(
        self, recommendation: TradeRecommendation
    ) -> tuple[MarketUniverseCandidate, ...]:
        run = self.session.get(AnalysisRun, recommendation.analysis_run_id)
        if run is None or not run.pipeline_run_id:
            return ()
        return tuple(
            self.session.scalars(
                select(MarketUniverseCandidate)
                .join(
                    AnalysisRun,
                    MarketUniverseCandidate.analysis_run_id == AnalysisRun.id,
                )
                .where(
                    AnalysisRun.pipeline_run_id == run.pipeline_run_id,
                    MarketUniverseCandidate.user_id == recommendation.user_id,
                    MarketUniverseCandidate.exchange == recommendation.exchange,
                )
                .order_by(
                    MarketUniverseCandidate.rank.nullslast(), MarketUniverseCandidate.id
                )
            )
        )

    def _selected_values(
        self, recommendation, candidate, recommendation_at, target_at, horizon, now
    ) -> dict[str, Any]:
        base = {
            "recommendation_id": recommendation.id,
            "user_id": recommendation.user_id,
            "exchange": recommendation.exchange,
            "market": recommendation.market,
            "horizon_minutes": horizon,
            "recommendation_at": recommendation_at,
            "target_at": target_at,
            "evaluated_at": now,
        }
        if recommendation.exchange != self.provider.exchange_code:
            return self._selected_partial(base, "UNSUPPORTED_EXCHANGE")
        if candidate is None:
            return self._selected_partial(base, "EXACT_CANDIDATE_UNAVAILABLE")
        if (
            candidate.user_id != recommendation.user_id
            or candidate.exchange != recommendation.exchange
            or candidate.market != recommendation.market
        ):
            return self._selected_partial(base, "SELECTED_CANDIDATE_MISMATCH")
        reference = self._positive_decimal(
            (candidate.feature_data or {}).get("latest_price")
        )
        if reference is None:
            return self._selected_partial(base, "REFERENCE_PRICE_UNAVAILABLE")
        end = self._historical_price(recommendation.market, target_at)
        if end is None:
            values = self._selected_partial(base, "HISTORICAL_PRICE_UNAVAILABLE")
            values.update(
                reference_price=reference, reference_price_source=REFERENCE_PRICE_SOURCE
            )
            return values
        end_price, end_at = end
        market_return = (end_price / reference - Decimal("1")) * Decimal("100")
        action = recommendation.action.strip().upper()
        aligned = (
            market_return
            if action == "BUY"
            else -market_return
            if action == "SELL"
            else None
        )
        if action == "HOLD":
            directional = "NOT_APPLICABLE"
        elif aligned is None:
            return self._selected_partial(base, "UNSUPPORTED_ACTION")
        else:
            directional = "WIN" if aligned > 0 else "LOSS" if aligned < 0 else "FLAT"
        return {
            **base,
            "reference_price": reference,
            "reference_price_source": REFERENCE_PRICE_SOURCE,
            "end_price": end_price,
            "end_price_at": end_at,
            "end_price_source": END_PRICE_SOURCE,
            "market_return_percentage": market_return,
            "action_aligned_return_percentage": aligned,
            "directional_result": directional,
            "evaluation_status": "COMPLETE",
            "safe_reason": None,
        }

    @staticmethod
    def _selected_partial(base: dict[str, Any], reason: str) -> dict[str, Any]:
        return {
            **base,
            "reference_price": None,
            "reference_price_source": UNAVAILABLE_SOURCE,
            "end_price": None,
            "end_price_at": None,
            "end_price_source": UNAVAILABLE_SOURCE,
            "market_return_percentage": None,
            "action_aligned_return_percentage": None,
            "directional_result": None,
            "evaluation_status": "PARTIAL",
            "safe_reason": reason,
        }

    def _candidate_values(
        self, recommendation, candidate, recommendation_at, target_at, horizon, now
    ) -> dict[str, Any]:
        feature_data = candidate.feature_data or {}
        base = {
            "recommendation_id": recommendation.id,
            "universe_candidate_id": candidate.id,
            "user_id": recommendation.user_id,
            "exchange": candidate.exchange,
            "market": candidate.market,
            "horizon_minutes": horizon,
            "recommendation_at": recommendation_at,
            "target_at": target_at,
            "rank": candidate.rank,
            "score": candidate.score,
            "selection_source": candidate.selection_source,
            "buy_eligible": candidate.buy_eligible,
            "sell_eligible": candidate.sell_eligible,
            "held": bool(feature_data.get("held")),
            "is_selected": candidate.id == recommendation.universe_candidate_id,
            "evaluated_at": now,
        }
        if candidate.exchange != self.provider.exchange_code:
            return {
                **base,
                "reference_price": None,
                "end_price": None,
                "end_price_at": None,
                "end_price_source": UNAVAILABLE_SOURCE,
                "market_return_percentage": None,
                "evaluation_status": "PARTIAL",
                "safe_reason": "UNSUPPORTED_EXCHANGE",
            }
        reference = self._positive_decimal(feature_data.get("latest_price"))
        if reference is None:
            return {
                **base,
                "reference_price": None,
                "end_price": None,
                "end_price_at": None,
                "end_price_source": UNAVAILABLE_SOURCE,
                "market_return_percentage": None,
                "evaluation_status": "PARTIAL",
                "safe_reason": "REFERENCE_PRICE_UNAVAILABLE",
            }
        end = self._historical_price(candidate.market, target_at)
        if end is None:
            return {
                **base,
                "reference_price": reference,
                "end_price": None,
                "end_price_at": None,
                "end_price_source": UNAVAILABLE_SOURCE,
                "market_return_percentage": None,
                "evaluation_status": "PARTIAL",
                "safe_reason": "HISTORICAL_PRICE_UNAVAILABLE",
            }
        end_price, end_at = end
        return {
            **base,
            "reference_price": reference,
            "end_price": end_price,
            "end_price_at": end_at,
            "end_price_source": END_PRICE_SOURCE,
            "market_return_percentage": (end_price / reference - Decimal("1"))
            * Decimal("100"),
            "evaluation_status": "COMPLETE",
            "safe_reason": None,
        }

    def _historical_price(
        self, market: str, target_at: datetime
    ) -> tuple[Decimal, datetime] | None:
        key = (market, target_at)
        if key in self._price_cache:
            return self._price_cache[key]
        try:
            rows = self.provider.get_minute_candles(
                market, unit=1, count=HISTORICAL_CANDLE_COUNT, to=target_at
            )
        except Exception:
            rows = []
        eligible: list[tuple[datetime, Decimal]] = []
        for row in rows:
            candle_at = self._parse_upbit_utc(row.get("candle_date_time_utc"))
            price = self._positive_decimal(row.get("trade_price"))
            if (
                candle_at is not None
                and price is not None
                and candle_at + timedelta(minutes=1) <= target_at
            ):
                eligible.append((candle_at, price))
        selected = max(eligible, default=None, key=lambda item: item[0])
        result = (selected[1], selected[0]) if selected else None
        self._price_cache[key] = result
        return result

    @staticmethod
    def _record_counts(counts, horizon_counts, prefix, existing, values) -> None:
        status = values["evaluation_status"].lower()
        counts[f"{prefix}_{status}"] += 1
        counts[f"{prefix}_{'new' if existing is None else 'update'}"] += 1
        if prefix == "selected":
            horizon_counts[status] += 1

    def _upsert(self, row, model, values) -> None:
        if row is None:
            self.session.add(model(**values))
            return
        for key, value in values.items():
            setattr(row, key, value)

    @staticmethod
    def _positive_decimal(value: object) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = Decimal(str(value).strip())
        except InvalidOperation, TypeError, ValueError:
            return None
        return parsed if parsed.is_finite() and parsed > 0 else None

    @staticmethod
    def _aware_utc(value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(UTC)

    @staticmethod
    def _parse_upbit_utc(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
