from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any

from sqlalchemy.orm import Session

from crypto_trading_bot.analysis.market_ranking import MarketRankingPolicy
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import (
    AnalysisRun,
    StrategyReplayCandidate,
    StrategyReplaySnapshot,
    User,
)


DATASET_SCHEMA_VERSION = "strategy-replay-dataset-v1"
POLICY_SIGNATURE_PREFIX = "strategy-replay-v1"
REPLAY_FEATURE_FIELDS = (
    "exchange",
    "market",
    "base_asset",
    "quote_asset",
    "latest_price",
    "liquidity",
    "quote_trade_value_24h",
    "market_event",
    "timeframes",
    "orderbook",
    "data_quality",
    "enough_candles",
    "held",
    "buy_eligible",
    "sell_eligible",
    "trading_supported",
    "position",
)


@dataclass(frozen=True)
class StrategyReplayCapture:
    snapshot: StrategyReplaySnapshot
    candidates: tuple[StrategyReplayCandidate, ...]


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def build_policy_data(
    settings: Settings, ranking_policy: MarketRankingPolicy
) -> dict[str, Any]:
    weights = getattr(ranking_policy, "weights", None)
    weight_data = (
        asdict(weights) if weights is not None and is_dataclass(weights) else {}
    )
    return _json_safe(
        {
            "dataset_schema_version": DATASET_SCHEMA_VERSION,
            "market_universe": {
                "mode": settings.market_universe_mode,
                "exchange": settings.market_universe_exchange.strip().upper(),
                "quote_asset": settings.market_universe_quote_asset.strip().upper(),
                "prefilter_n": settings.market_universe_prefilter_n,
                "top_n": settings.market_universe_top_n,
                "minimum_24h_trade_value_krw": settings.market_universe_min_24h_trade_value_krw,
                "exclude_warnings": settings.market_universe_exclude_warnings,
                "exclude_cautions": settings.market_universe_exclude_cautions,
                "market_blocklist": sorted(settings.market_block_list),
                "analysis_timeframes": settings.analysis_timeframe_list,
                "analysis_candle_count": settings.analysis_candle_count,
            },
            "ranking": {
                "policy_name": type(ranking_policy).__name__,
                "weights": weight_data,
            },
        }
    )


def policy_signature(policy_data: dict[str, Any]) -> str:
    canonical = json.dumps(
        _json_safe(policy_data),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return f"{POLICY_SIGNATURE_PREFIX}:{sha256(canonical.encode('utf-8')).hexdigest()}"


class StrategyReplayDatasetService:
    """Persist already-collected universe inputs without external data access."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def capture(
        self,
        *,
        analysis_run: AnalysisRun,
        user: User,
        exchange: str,
        quote_asset: str,
        settings: Settings,
        ranking_policy: MarketRankingPolicy,
        research_candidates: list[dict[str, Any]],
        liquidity_prefilter: list[dict[str, Any]],
        ranked_candidates: list[dict[str, Any]],
        final_candidates: list[dict[str, Any]],
    ) -> StrategyReplayCapture:
        if not analysis_run.pipeline_run_id:
            raise ValueError("Strategy replay dataset requires pipeline_run_id")
        policy_data = build_policy_data(settings, ranking_policy)
        snapshot = StrategyReplaySnapshot(
            analysis_run_id=analysis_run.id,
            pipeline_run_id=analysis_run.pipeline_run_id,
            user_id=user.id,
            exchange=exchange,
            quote_asset=quote_asset,
            dataset_schema_version=DATASET_SCHEMA_VERSION,
            policy_signature=policy_signature(policy_data),
            policy_data=policy_data,
            research_candidate_count=len(research_candidates),
            prefilter_candidate_count=len(liquidity_prefilter),
            ranked_candidate_count=len(ranked_candidates),
            final_candidate_count=len(final_candidates),
            captured_at=datetime.now(UTC),
        )
        self.session.add(snapshot)
        self.session.flush()
        prefilter_ranks = {
            str(candidate["market"]): rank
            for rank, candidate in enumerate(liquidity_prefilter, start=1)
        }
        original_ranking = {
            str(candidate["market"]): (rank, candidate.get("score"))
            for rank, candidate in enumerate(ranked_candidates, start=1)
        }
        final_by_market = {
            str(candidate["market"]): candidate for candidate in final_candidates
        }
        rows: list[StrategyReplayCandidate] = []
        for candidate in research_candidates:
            market = str(candidate["market"])
            original_rank, original_score = original_ranking.get(market, (None, None))
            final = final_by_market.get(market)
            feature_source = {**(final or {}), **candidate}
            row = StrategyReplayCandidate(
                strategy_replay_snapshot_id=snapshot.id,
                analysis_run_id=analysis_run.id,
                user_id=user.id,
                exchange=exchange,
                market=market,
                base_asset=str(candidate["base_asset"]),
                quote_asset=str(candidate["quote_asset"]),
                in_prefilter=market in prefilter_ranks,
                prefilter_rank=prefilter_ranks.get(market),
                held=bool(candidate.get("held")),
                buy_eligible=bool(candidate.get("buy_eligible")),
                sell_eligible=bool(candidate.get("sell_eligible")),
                trading_supported=bool(candidate.get("trading_supported", True)),
                original_rank=original_rank,
                original_score=original_score,
                final_selected=final is not None,
                final_rank=final.get("rank") if final is not None else None,
                selection_source=(
                    str(final["selection_source"]) if final is not None else None
                ),
                quote_trade_value_24h=(
                    Decimal(str(candidate["quote_trade_value_24h"]))
                    if candidate.get("quote_trade_value_24h") is not None
                    else None
                ),
                feature_data=_json_safe(
                    {
                        key: feature_source[key]
                        for key in REPLAY_FEATURE_FIELDS
                        if key in feature_source
                    }
                ),
            )
            self.session.add(row)
            rows.append(row)
        self.session.flush()
        return StrategyReplayCapture(snapshot=snapshot, candidates=tuple(rows))
