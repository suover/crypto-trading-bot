from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import logging
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.analysis.indicators import calculate_market_indicators
from crypto_trading_bot.analysis.market_ranking import (
    HeuristicMarketRankingPolicy,
    MarketRankingPolicy,
)
from crypto_trading_bot.config.settings import Settings, get_settings
from crypto_trading_bot.db.models import (
    AccountSnapshot,
    AnalysisRun,
    MarketCandle,
    MarketSnapshot,
    MarketUniverseCandidate,
    User,
)
from crypto_trading_bot.exchange.market_data import (
    ExchangeMarketDataProvider,
    ExchangeMarketInfo,
    ExchangeTicker,
)
from crypto_trading_bot.exchange.upbit_market_data_provider import (
    UpbitMarketDataProvider,
)
from crypto_trading_bot.services.exchange_market_registry_service import (
    ExchangeMarketRegistryService,
)
from crypto_trading_bot.services.market_candle_service import MarketCandleService
from crypto_trading_bot.services.market_data_context_service import (
    MarketDataContextService,
)
from crypto_trading_bot.services.pipeline_identity import get_pipeline_run_id


MIN_CANDLES_FOR_ADVICE = 20
MIN_RECOMMENDED_ORDER_AMOUNT_KRW = Decimal("5000")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketUniverseBuildResult:
    analysis_run: AnalysisRun
    candidates: list[MarketUniverseCandidate]
    exchange: str
    quote_asset: str
    total_market_count: int
    warning_excluded_count: int
    caution_excluded_count: int
    blocklist_excluded_count: int
    liquidity_excluded_count: int
    prefilter_count: int
    data_collection_count: int
    ranked_count: int
    holdings_added_count: int


class MarketUniverseService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        market_data_provider: ExchangeMarketDataProvider | None = None,
        registry_service: ExchangeMarketRegistryService | None = None,
        candle_service: MarketCandleService | None = None,
        ranking_policy: MarketRankingPolicy | None = None,
        canary_run_reserver: Callable[[AnalysisRun], object | None] | None = None,
        canary_max_buy_order_amount_krw: Decimal | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.provider = market_data_provider or UpbitMarketDataProvider()
        self.registry = registry_service or ExchangeMarketRegistryService(
            session, self.settings
        )
        self.candle_service = candle_service or MarketCandleService(
            session, market_data_provider=self.provider
        )
        self.ranking_policy = ranking_policy or HeuristicMarketRankingPolicy()
        self.canary_run_reserver = canary_run_reserver
        self.canary_max_buy_order_amount_krw = canary_max_buy_order_amount_krw

    def build_and_persist(
        self,
        user_name: str = "Minsu",
        pipeline_run_id: str | None = None,
    ) -> MarketUniverseBuildResult:
        pipeline_id = get_pipeline_run_id(pipeline_run_id)
        user = self.session.scalar(select(User).where(User.name == user_name))
        if user is None:
            raise ValueError(f"User not found. name={user_name}")
        exchange = self.settings.market_universe_exchange.strip().upper()
        quote_asset = self.settings.market_universe_quote_asset.strip().upper()
        if exchange != self.provider.exchange_code:
            raise ValueError(
                f"Configured universe exchange has no provider. exchange={exchange}"
            )
        analysis_run = AnalysisRun(
            user_id=user.id,
            pipeline_run_id=pipeline_id,
            run_type="MARKET_UNIVERSE",
            trading_mode=self.settings.trading_mode,
            status="STARTED",
        )
        self.session.add(analysis_run)
        self.session.flush()
        self.session.commit()
        try:
            reserved_canary_run = None
            if self.canary_run_reserver is not None:
                reserved_canary_run = self.canary_run_reserver(analysis_run)
            result = self._build(
                analysis_run=analysis_run,
                user=user,
                exchange=exchange,
                quote_asset=quote_asset,
                pipeline_run_id=pipeline_id,
                canary_buy_cap=(
                    self.canary_max_buy_order_amount_krw
                    if reserved_canary_run is not None
                    else None
                ),
            )
            analysis_run.status = "SUCCESS"
            analysis_run.finished_at = datetime.now(UTC)
            self.session.commit()
            return result
        except Exception as error:
            self.session.rollback()
            analysis_run = self.session.get(AnalysisRun, analysis_run.id)
            if analysis_run is not None:
                analysis_run.status = "FAILED"
                analysis_run.error_message = str(error)
                analysis_run.finished_at = datetime.now(UTC)
                self.session.commit()
            raise

    def _build(
        self,
        *,
        analysis_run: AnalysisRun,
        user: User,
        exchange: str,
        quote_asset: str,
        pipeline_run_id: str | None,
        canary_buy_cap: Decimal | None,
    ) -> MarketUniverseBuildResult:
        balances = self._load_balances(user.id, exchange, pipeline_run_id)
        excluded_assets = self.settings.portfolio_excluded_asset_set
        held_assets = {
            currency
            for currency, values in balances.items()
            if currency != quote_asset
            and currency not in excluded_assets
            and values["total"] > 0
        }
        if self.settings.market_universe_mode == "STATIC":
            descriptors = [
                descriptor
                for descriptor in self._static_descriptors(exchange)
                if descriptor.base_asset not in excluded_assets
            ]
            tickers = self.provider.get_tickers(
                markets=[descriptor.market for descriptor in descriptors]
            )
        else:
            descriptors = self.provider.list_markets(quote_asset=quote_asset)
            tickers = self.provider.get_tickers(quote_asset=quote_asset)
        ticker_by_market = {item.market: item for item in tickers}
        markets = [
            descriptor
            for descriptor in descriptors
            if descriptor.quote_asset == quote_asset
            and descriptor.base_asset not in excluded_assets
        ]
        warning_excluded = 0
        caution_excluded = 0
        blocklist_excluded = 0
        liquidity_excluded = 0
        eligible: list[dict[str, Any]] = []
        blocklist = set(self.settings.market_block_list)
        minimum_liquidity = self.settings.market_universe_min_24h_trade_value_krw
        for descriptor in markets:
            ticker = ticker_by_market.get(descriptor.market)
            held = descriptor.base_asset in held_assets
            if self.settings.market_universe_mode == "STATIC":
                eligible.append(self._base_candidate(descriptor, ticker, held, True))
                continue
            buy_eligible = ticker is not None and ticker.trade_price is not None
            if descriptor.is_warning and self.settings.market_universe_exclude_warnings:
                warning_excluded += 1
                buy_eligible = False
            if descriptor.is_caution and self.settings.market_universe_exclude_cautions:
                caution_excluded += 1
                buy_eligible = False
            if descriptor.market in blocklist:
                blocklist_excluded += 1
                buy_eligible = False
            liquidity = ticker.quote_trade_value_24h if ticker else None
            if liquidity is None or liquidity < minimum_liquidity:
                liquidity_excluded += 1
                buy_eligible = False
            if buy_eligible or held:
                eligible.append(
                    self._base_candidate(descriptor, ticker, held, buy_eligible)
                )
        discovered_assets = {descriptor.base_asset for descriptor in markets}
        if self.settings.market_universe_mode == "DYNAMIC":
            for held_asset in sorted(held_assets - discovered_assets):
                eligible.append(
                    {
                        "exchange": exchange,
                        "market": f"{quote_asset}-{held_asset}",
                        "base_asset": held_asset,
                        "quote_asset": quote_asset,
                        "buy_eligible": False,
                        "sell_eligible": False,
                        "held": True,
                        "trading_supported": False,
                        "latest_price": None,
                        "liquidity": {
                            "quote_trade_value_24h": None,
                            "base_trade_volume_24h": None,
                        },
                        "quote_trade_value_24h": None,
                        "market_event": {
                            "warning": False,
                            "caution": False,
                            "raw": {"trading_supported": False},
                        },
                    }
                )
        if self.settings.market_universe_mode == "STATIC":
            liquidity_prefilter = eligible
            data_collection_candidates = eligible
        else:
            liquidity_prefilter = sorted(
                (candidate for candidate in eligible if candidate["buy_eligible"]),
                key=lambda value: Decimal(value["quote_trade_value_24h"] or "0"),
                reverse=True,
            )[: self.settings.market_universe_prefilter_n]
            data_collection_by_market = {
                candidate["market"]: candidate for candidate in liquidity_prefilter
            }
            for candidate in eligible:
                if candidate["held"]:
                    data_collection_by_market.setdefault(candidate["market"], candidate)
            data_collection_candidates = list(data_collection_by_market.values())
        data_collection_markets = [
            candidate["market"]
            for candidate in data_collection_candidates
            if candidate.get("trading_supported", True)
        ]
        collection_results = self.candle_service.collect_timeframes_for_markets(
            data_collection_markets,
            self.settings.analysis_timeframe_list,
            self.settings.analysis_candle_count,
            commit=False,
        )
        orderbooks = self._load_orderbooks(data_collection_markets)
        for candidate in data_collection_candidates:
            candidate["timeframes"] = (
                self._timeframe_features(
                    candidate["market"], collection_results.get(candidate["market"], {})
                )
                if candidate.get("trading_supported", True)
                else {
                    timeframe: {
                        "data_quality": "UNAVAILABLE",
                        "candle_count": 0,
                        "trend_label": "판단 보류",
                        "reason": "trading_not_supported",
                    }
                    for timeframe in self.settings.analysis_timeframe_list
                }
            )
            candidate["orderbook"] = orderbooks.get(
                candidate["market"],
                {"available": False, "reason": "market_not_returned"},
            )
            candidate["data_quality"] = self._overall_quality(candidate["timeframes"])
            candidate["enough_candles"] = any(
                value["data_quality"] == "SUFFICIENT"
                for value in candidate["timeframes"].values()
            )
        if self.settings.market_universe_mode == "STATIC":
            ranked: list[dict[str, Any]] = []
            final = []
            for rank, candidate in enumerate(data_collection_candidates, start=1):
                final.append(
                    {
                        **candidate,
                        "rank": rank,
                        "score": None,
                        "selection_source": "STATIC",
                    }
                )
            ranked_count = len(final)
            holdings_added_count = 0
        else:
            rankable = [
                candidate
                for candidate in liquidity_prefilter
                if candidate["buy_eligible"] and candidate["enough_candles"]
            ]
            ranked = self.ranking_policy.rank(rankable)
            top_ranked = ranked[: self.settings.market_universe_top_n]
            final_by_market: dict[str, dict[str, Any]] = {}
            for rank, candidate in enumerate(top_ranked, start=1):
                final_by_market[candidate["market"]] = {
                    **candidate,
                    "rank": rank,
                    "selection_source": "RANKED",
                }
            holdings_added_count = 0
            for candidate in data_collection_candidates:
                if not candidate["held"] or candidate["market"] in final_by_market:
                    continue
                holdings_added_count += 1
                final_by_market[candidate["market"]] = {
                    **candidate,
                    "rank": None,
                    "score": None,
                    "selection_source": "HELD",
                    "buy_eligible": False,
                }
            final = list(final_by_market.values())
            ranked_count = len(top_ranked)
        persisted: list[MarketUniverseCandidate] = []
        for candidate in final:
            registry_market = self.registry.get_market(exchange, candidate["market"])
            candidate["coingecko_id"] = (
                registry_market.coingecko_id if registry_market is not None else None
            )
            existing_max_order_amount = (
                self.registry.calculate_final_max_order_amount(registry_market)
                if registry_market is not None
                else self.registry.calculate_default_max_order_amount(exchange)
            )
            candidate["max_order_amount_krw"] = str(
                min(existing_max_order_amount, canary_buy_cap)
                if canary_buy_cap is not None
                else existing_max_order_amount
            )
            candidate["minimum_order_amount_krw"] = str(
                MIN_RECOMMENDED_ORDER_AMOUNT_KRW
            )
            balance = balances.get(
                candidate["base_asset"],
                {
                    "balance": Decimal("0"),
                    "locked": Decimal("0"),
                    "total": Decimal("0"),
                    "avg": Decimal("0"),
                },
            )
            candidate["position"] = self._position(candidate, balance)
            candidate["coin_balance"] = str(balance["balance"])
            candidate["avg_buy_price"] = (
                str(balance["avg"]) if balance["avg"] > 0 else None
            )
            candidate["quote_balance_krw"] = str(
                balances.get(quote_asset, {"balance": Decimal("0")})["balance"]
            )
            feature_data = self._json_safe(candidate)
            row = MarketUniverseCandidate(
                analysis_run_id=analysis_run.id,
                user_id=user.id,
                exchange=exchange,
                market=candidate["market"],
                base_asset=candidate["base_asset"],
                quote_asset=quote_asset,
                rank=candidate["rank"],
                score=candidate.get("score"),
                selection_source=candidate["selection_source"],
                buy_eligible=bool(candidate["buy_eligible"]),
                sell_eligible=bool(candidate["sell_eligible"]),
                quote_trade_value_24h=(
                    Decimal(candidate["quote_trade_value_24h"])
                    if candidate["quote_trade_value_24h"] is not None
                    else None
                ),
                market_event_data=candidate["market_event"],
                feature_data=feature_data,
            )
            self.session.add(row)
            persisted.append(row)
            ticker = ticker_by_market.get(candidate["market"])
            self.session.add(
                MarketSnapshot(
                    analysis_run_id=analysis_run.id,
                    user_id=user.id,
                    exchange=exchange,
                    market=candidate["market"],
                    current_price=ticker.trade_price if ticker else None,
                    change_rate=ticker.signed_change_rate if ticker else None,
                    volume_24h=ticker.quote_trade_value_24h if ticker else None,
                    raw_data=ticker.raw_data if ticker else None,
                )
            )
        self.session.flush()
        if self.settings.strategy_replay_dataset_enabled:
            from crypto_trading_bot.services.strategy_replay_dataset_service import (
                StrategyReplayDatasetService,
            )

            replay_prefilter = (
                liquidity_prefilter
                if self.settings.market_universe_mode == "DYNAMIC"
                else []
            )
            replay_ranked = (
                ranked if self.settings.market_universe_mode == "DYNAMIC" else []
            )
            try:
                with self.session.begin_nested():
                    StrategyReplayDatasetService(self.session).capture(
                        analysis_run=analysis_run,
                        user=user,
                        exchange=exchange,
                        quote_asset=quote_asset,
                        settings=self.settings,
                        ranking_policy=self.ranking_policy,
                        research_candidates=data_collection_candidates,
                        liquidity_prefilter=replay_prefilter,
                        ranked_candidates=replay_ranked,
                        final_candidates=final,
                    )
            except Exception:
                logger.exception(
                    "Strategy replay dataset persistence failed; universe remains valid. "
                    "analysis_run_id=%s pipeline_run_id=%s",
                    analysis_run.id,
                    pipeline_run_id,
                )
        return MarketUniverseBuildResult(
            analysis_run=analysis_run,
            candidates=persisted,
            exchange=exchange,
            quote_asset=quote_asset,
            total_market_count=len(markets),
            warning_excluded_count=warning_excluded,
            caution_excluded_count=caution_excluded,
            blocklist_excluded_count=blocklist_excluded,
            liquidity_excluded_count=liquidity_excluded,
            prefilter_count=len(liquidity_prefilter),
            data_collection_count=len(data_collection_candidates),
            ranked_count=ranked_count,
            holdings_added_count=holdings_added_count,
        )

    def _static_descriptors(self, exchange: str) -> list[ExchangeMarketInfo]:
        return [
            ExchangeMarketInfo(
                exchange=exchange,
                market=row.market,
                base_asset=row.base_asset,
                quote_asset=row.quote_asset,
                korean_name=None,
                english_name=None,
                is_warning=False,
                is_caution=False,
                market_event={},
                raw_data={},
            )
            for row in self.registry.load_allowed_active_markets_for_exchange(exchange)
        ]

    @staticmethod
    def _base_candidate(
        descriptor: ExchangeMarketInfo,
        ticker: ExchangeTicker | None,
        held: bool,
        buy_eligible: bool,
    ) -> dict[str, Any]:
        valid_ticker = (
            ticker is not None
            and ticker.trade_price is not None
            and ticker.trade_price > 0
        )
        return {
            "exchange": descriptor.exchange,
            "market": descriptor.market,
            "base_asset": descriptor.base_asset,
            "quote_asset": descriptor.quote_asset,
            "buy_eligible": buy_eligible and valid_ticker,
            "sell_eligible": held and valid_ticker,
            "held": held,
            "trading_supported": True,
            "latest_price": str(ticker.trade_price)
            if ticker and ticker.trade_price
            else None,
            "liquidity": {
                "quote_trade_value_24h": (
                    str(ticker.quote_trade_value_24h)
                    if ticker and ticker.quote_trade_value_24h is not None
                    else None
                ),
                "base_trade_volume_24h": (
                    str(ticker.base_trade_volume_24h)
                    if ticker and ticker.base_trade_volume_24h is not None
                    else None
                ),
            },
            "quote_trade_value_24h": (
                str(ticker.quote_trade_value_24h)
                if ticker and ticker.quote_trade_value_24h is not None
                else None
            ),
            "market_event": {
                "warning": descriptor.is_warning,
                "caution": descriptor.is_caution,
                "raw": descriptor.market_event,
            },
        }

    def _load_balances(
        self, user_id: int, exchange: str, pipeline_run_id: str | None
    ) -> dict[str, dict[str, Decimal]]:
        statement = (
            select(AccountSnapshot)
            .join(AnalysisRun, AnalysisRun.id == AccountSnapshot.analysis_run_id)
            .where(
                AccountSnapshot.user_id == user_id, AccountSnapshot.exchange == exchange
            )
            .order_by(AccountSnapshot.created_at.desc(), AccountSnapshot.id.desc())
        )
        if pipeline_run_id is not None:
            statement = statement.where(
                AnalysisRun.pipeline_run_id == pipeline_run_id,
                AnalysisRun.run_type.in_(("ACCOUNT_SNAPSHOT", "MANUAL")),
                AnalysisRun.status == "SUCCESS",
            )
        result: dict[str, dict[str, Decimal]] = {}
        for row in self.session.scalars(statement):
            if row.currency in result:
                continue
            balance = Decimal(str(row.balance or 0))
            locked = Decimal(str(row.locked or 0))
            result[row.currency] = {
                "balance": balance,
                "locked": locked,
                "total": balance + locked,
                "avg": Decimal(str(row.avg_buy_price or 0)),
            }
        return result

    def _load_orderbooks(self, markets: list[str]) -> dict[str, dict[str, Any]]:
        if not markets or not self.settings.upbit_orderbook_enabled:
            return {}
        try:
            rows = self.provider.get_orderbooks(
                markets, count=self.settings.upbit_orderbook_count
            )
        except Exception:
            return {}
        return {
            str(row["market"]): MarketDataContextService._normalize_orderbook(row)
            for row in rows
            if isinstance(row.get("market"), str)
        }

    def _timeframe_features(
        self, market: str, collection_status: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for timeframe in self.settings.analysis_timeframe_list:
            status = collection_status.get(timeframe, {})
            if status.get("status") == "UNAVAILABLE":
                result[timeframe] = {
                    "data_quality": "UNAVAILABLE",
                    "candle_count": 0,
                    "trend_label": "판단 보류",
                    "error_type": status.get("error_type"),
                }
                continue
            candle_type, candle_unit = MarketCandleService._timeframe_storage(timeframe)
            candles = list(
                reversed(
                    list(
                        self.session.scalars(
                            select(MarketCandle)
                            .where(
                                MarketCandle.exchange == self.provider.exchange_code,
                                MarketCandle.market == market,
                                MarketCandle.candle_type == candle_type,
                                MarketCandle.candle_unit == candle_unit,
                            )
                            .order_by(MarketCandle.candle_at.desc())
                            .limit(self.settings.analysis_candle_count)
                        )
                    )
                )
            )
            if len(candles) < MIN_CANDLES_FOR_ADVICE:
                result[timeframe] = {
                    "data_quality": "INSUFFICIENT",
                    "candle_count": len(candles),
                    "trend_label": "판단 보류",
                }
                continue
            indicator = calculate_market_indicators(
                market,
                candle_unit,
                [Decimal(str(row.trade_price)) for row in candles],
                [Decimal(str(row.candle_acc_trade_volume)) for row in candles],
                [Decimal(str(row.high_price)) for row in candles],
                [Decimal(str(row.low_price)) for row in candles],
            )
            result[timeframe] = {
                "data_quality": "SUFFICIENT",
                "candle_count": indicator.candle_count,
                "latest_price": str(indicator.latest_price),
                "sma_5": self._optional_string(indicator.sma_5),
                "sma_20": self._optional_string(indicator.sma_20),
                "ema_5": self._optional_string(indicator.ema_5),
                "ema_20": self._optional_string(indicator.ema_20),
                "rsi_14": self._optional_string(indicator.rsi_14),
                "recent_change_rate": self._optional_string(
                    indicator.recent_10_candle_change_rate
                ),
                "volume_ratio": self._optional_string(indicator.volume_ratio_5_to_20),
                "trend_label": indicator.trend_label,
                "macd": self._optional_string(indicator.macd),
                "macd_signal": self._optional_string(indicator.macd_signal),
                "macd_histogram": self._optional_string(indicator.macd_histogram),
                "atr_14": self._optional_string(indicator.atr_14),
                "atr_percentage": self._optional_string(indicator.atr_percentage),
                "realized_volatility": self._optional_string(
                    indicator.realized_volatility
                ),
                "max_drawdown": self._optional_string(indicator.max_drawdown),
            }
        return result

    @staticmethod
    def _overall_quality(timeframes: dict[str, dict[str, Any]]) -> str:
        sufficient = sum(
            value.get("data_quality") == "SUFFICIENT" for value in timeframes.values()
        )
        if sufficient == len(timeframes):
            return "SUFFICIENT"
        if sufficient:
            return "PARTIAL"
        return "INSUFFICIENT"

    @staticmethod
    def _position(
        candidate: dict[str, Any], balance: dict[str, Decimal]
    ) -> dict[str, Any]:
        price = Decimal(candidate["latest_price"] or "0")
        current_value = balance["total"] * price if price > 0 else None
        estimated_cost_basis = (
            balance["total"] * balance["avg"] if balance["avg"] > 0 else None
        )
        unrealized = (
            current_value - estimated_cost_basis
            if current_value is not None and estimated_cost_basis is not None
            else None
        )
        unrealized_percentage = (
            unrealized / estimated_cost_basis * Decimal("100")
            if unrealized is not None and estimated_cost_basis > 0
            else None
        )
        return {
            "market": candidate["market"],
            "base_asset": candidate["base_asset"],
            "available_balance": str(balance["balance"]),
            "locked_balance": str(balance["locked"]),
            "total_balance": str(balance["total"]),
            "avg_buy_price": str(balance["avg"]) if balance["avg"] > 0 else None,
            "current_value_krw": (
                str(current_value) if current_value is not None else None
            ),
            "estimated_cost_basis_krw": (
                str(estimated_cost_basis) if estimated_cost_basis is not None else None
            ),
            "unrealized_pnl_krw": str(unrealized) if unrealized is not None else None,
            "unrealized_pnl_percentage": (
                str(unrealized_percentage)
                if unrealized_percentage is not None
                else None
            ),
        }

    @staticmethod
    def _optional_string(value: Decimal | None) -> str | None:
        return str(value) if value is not None else None

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._json_safe(item) for item in value]
        return value
