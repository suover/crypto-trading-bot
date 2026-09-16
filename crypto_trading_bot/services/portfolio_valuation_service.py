from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import Settings, get_settings
from crypto_trading_bot.db.models import (
    AccountSnapshot,
    AnalysisRun,
    MarketSnapshot,
    MarketUniverseCandidate,
    PortfolioPositionSnapshot,
    PortfolioSnapshot,
    User,
)
from crypto_trading_bot.market_data.coingecko_client import CoinGeckoClient
from crypto_trading_bot.services.pipeline_identity import get_pipeline_run_id
from crypto_trading_bot.services.runtime_user_resolver import RuntimeUserResolver


ACCOUNT_SNAPSHOT_RUN_TYPES = ("ACCOUNT_SNAPSHOT", "MANUAL")


@dataclass(frozen=True)
class PortfolioValuationResult:
    analysis_run: AnalysisRun
    portfolio_snapshot: PortfolioSnapshot
    positions: tuple[PortfolioPositionSnapshot, ...]
    already_captured: bool


class PortfolioValuationService:
    """Create an account-level valuation from one persisted pipeline only."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        coingecko_client: CoinGeckoClient | None = None,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.coingecko_client = coingecko_client or CoinGeckoClient(self.settings)
        self.now_fn = now_fn

    def capture(
        self,
        user_id: int,
        pipeline_run_id: str | None = None,
        *,
        exchange: str = "UPBIT",
        quote_asset: str = "KRW",
    ) -> PortfolioValuationResult:
        pipeline_id = get_pipeline_run_id(pipeline_run_id)
        if pipeline_id is None:
            raise ValueError("Portfolio valuation requires pipeline_run_id")
        normalized_exchange = exchange.strip().upper()
        normalized_quote = quote_asset.strip().upper()
        user = RuntimeUserResolver(self.session).resolve(user_id)

        existing = self._get_existing(user.id, normalized_exchange, pipeline_id)
        if existing is not None:
            return existing

        analysis_run = AnalysisRun(
            user_id=user.id,
            pipeline_run_id=pipeline_id,
            run_type="PORTFOLIO_VALUATION",
            trading_mode=self.settings.trading_mode,
            status="STARTED",
        )
        self.session.add(analysis_run)
        self.session.flush()
        analysis_run_id = analysis_run.id
        self.session.commit()

        try:
            result = self._capture(
                analysis_run=analysis_run,
                user=user,
                pipeline_run_id=pipeline_id,
                exchange=normalized_exchange,
                quote_asset=normalized_quote,
            )
            analysis_run.status = "SUCCESS"
            analysis_run.finished_at = self.now_fn()
            self.session.commit()
            return result
        except Exception as error:
            self.session.rollback()
            failed_run = self.session.get(AnalysisRun, analysis_run_id)
            if failed_run is not None:
                failed_run.status = "FAILED"
                failed_run.error_message = type(error).__name__
                failed_run.finished_at = self.now_fn()
                self.session.commit()
            raise

    def _capture(
        self,
        *,
        analysis_run: AnalysisRun,
        user: User,
        pipeline_run_id: str,
        exchange: str,
        quote_asset: str,
    ) -> PortfolioValuationResult:
        account_run_id = self._get_account_run_id(user.id, exchange, pipeline_run_id)
        if account_run_id is None:
            raise ValueError("No account snapshots found for portfolio pipeline")
        universe_run_id = self._get_universe_run_id(user.id, pipeline_run_id)
        if universe_run_id is None:
            raise ValueError("No market universe found for portfolio pipeline")

        account_rows = list(
            self.session.scalars(
                select(AccountSnapshot)
                .where(
                    AccountSnapshot.analysis_run_id == account_run_id,
                    AccountSnapshot.user_id == user.id,
                    AccountSnapshot.exchange == exchange,
                )
                .order_by(AccountSnapshot.id.desc())
            )
        )
        if not account_rows:
            raise ValueError("Selected account run has no account snapshots")
        candidates = list(
            self.session.scalars(
                select(MarketUniverseCandidate)
                .where(
                    MarketUniverseCandidate.analysis_run_id == universe_run_id,
                    MarketUniverseCandidate.user_id == user.id,
                    MarketUniverseCandidate.exchange == exchange,
                    MarketUniverseCandidate.quote_asset == quote_asset,
                )
                .order_by(
                    MarketUniverseCandidate.rank.asc().nulls_last(),
                    MarketUniverseCandidate.id,
                )
            )
        )
        market_rows = list(
            self.session.scalars(
                select(MarketSnapshot)
                .where(
                    MarketSnapshot.analysis_run_id == universe_run_id,
                    MarketSnapshot.user_id == user.id,
                    MarketSnapshot.exchange == exchange,
                )
                .order_by(MarketSnapshot.id.desc())
            )
        )

        account_by_currency: dict[str, AccountSnapshot] = {}
        invalid_account_data = False
        for row in account_rows:
            currency = row.currency.strip().upper()
            if not currency or currency in account_by_currency:
                invalid_account_data = True
                continue
            account_by_currency[currency] = row

        candidate_by_asset: dict[str, MarketUniverseCandidate] = {}
        duplicate_candidate_assets: set[str] = set()
        for row in candidates:
            asset = row.base_asset.strip().upper()
            if asset in candidate_by_asset:
                duplicate_candidate_assets.add(asset)
                continue
            candidate_by_asset[asset] = row

        market_snapshot_by_market: dict[str, MarketSnapshot] = {}
        for row in market_rows:
            market_snapshot_by_market.setdefault(row.market, row)

        cash_row = account_by_currency.get(quote_asset)
        cash_available = (
            self._non_negative_decimal(cash_row.balance) if cash_row else None
        )
        cash_locked = self._non_negative_decimal(cash_row.locked) if cash_row else None
        cash_total = (
            cash_available + cash_locked
            if cash_available is not None and cash_locked is not None
            else None
        )
        if cash_row is None or cash_total is None:
            invalid_account_data = True

        excluded_assets = self.settings.portfolio_excluded_asset_set
        upbit_price_by_asset: dict[
            str, tuple[str | None, MarketSnapshot | None, Decimal | None]
        ] = {}
        held_unpriced_assets: list[str] = []
        for currency, account_row in account_by_currency.items():
            if currency == quote_asset:
                continue
            available = self._non_negative_decimal(account_row.balance)
            locked = self._non_negative_decimal(account_row.locked)
            if available is None or locked is None or available + locked <= 0:
                continue
            candidate = (
                None
                if currency in duplicate_candidate_assets
                else candidate_by_asset.get(currency)
            )
            market = candidate.market if candidate is not None else None
            if currency in excluded_assets:
                upbit_price_by_asset[currency] = (market, None, None)
                continue
            market_snapshot = (
                market_snapshot_by_market.get(market) if market is not None else None
            )
            mark_price = (
                self._positive_decimal(market_snapshot.current_price)
                if market_snapshot is not None
                else None
            )
            upbit_price_by_asset[currency] = (market, market_snapshot, mark_price)
            if mark_price is None:
                held_unpriced_assets.append(currency)
        coingecko_prices = self._get_coingecko_prices(held_unpriced_assets)

        position_values: list[dict[str, object]] = []
        priced_positions_value = Decimal("0")
        positions_cost_basis = Decimal("0")
        unpriced_count = 0
        missing_cost_basis_count = 0
        invalid_position_data = False

        for currency, account_row in account_by_currency.items():
            if currency == quote_asset:
                continue
            available = self._non_negative_decimal(account_row.balance)
            locked = self._non_negative_decimal(account_row.locked)
            if available is None or locked is None:
                invalid_position_data = True
                continue
            total_quantity = available + locked
            if total_quantity <= 0:
                continue

            # A persisted same-pipeline ticker is valuation evidence even when
            # trading policy disallows new orders for the market.
            market, market_snapshot, mark_price = upbit_price_by_asset.get(
                currency, (None, None, None)
            )
            excluded = currency in excluded_assets
            price_source = "POLICY_EXCLUDED" if excluded else "MARKET_SNAPSHOT"
            if mark_price is None and not excluded:
                mark_price = coingecko_prices.get(currency)
                price_source = "COINGECKO" if mark_price is not None else "UNAVAILABLE"
            market_value = (
                total_quantity * mark_price if mark_price is not None else None
            )
            avg_buy_price = self._positive_decimal(account_row.avg_buy_price)
            estimated_cost_basis = (
                total_quantity * avg_buy_price
                if avg_buy_price is not None and not excluded
                else None
            )
            unrealized_pnl = (
                market_value - estimated_cost_basis
                if market_value is not None and estimated_cost_basis is not None
                else None
            )
            unrealized_percentage = (
                unrealized_pnl / estimated_cost_basis * Decimal("100")
                if unrealized_pnl is not None and estimated_cost_basis > 0
                else None
            )
            if market_value is None and not excluded:
                unpriced_count += 1
            elif market_value is not None:
                priced_positions_value += market_value
            if estimated_cost_basis is None and not excluded:
                missing_cost_basis_count += 1
            elif estimated_cost_basis is not None:
                positions_cost_basis += estimated_cost_basis
            position_values.append(
                {
                    "account_snapshot_id": account_row.id,
                    "market_snapshot_id": (
                        market_snapshot.id if market_snapshot is not None else None
                    ),
                    "exchange": exchange,
                    "market": market,
                    "currency": currency,
                    "available_quantity": available,
                    "locked_quantity": locked,
                    "total_quantity": total_quantity,
                    "avg_buy_price": avg_buy_price,
                    "mark_price": mark_price,
                    "market_value_krw": market_value,
                    "estimated_cost_basis_krw": estimated_cost_basis,
                    "unrealized_pnl_krw": unrealized_pnl,
                    "unrealized_pnl_percentage": unrealized_percentage,
                    "valuation_status": (
                        "EXCLUDED"
                        if excluded
                        else "PRICED"
                        if market_value is not None
                        else "UNPRICED"
                    ),
                    "price_source": price_source,
                }
            )

        all_positions_priced = not invalid_position_data and unpriced_count == 0
        valuation_complete = (
            not invalid_account_data and cash_total is not None and all_positions_priced
        )
        all_cost_basis_known = (
            not invalid_position_data and missing_cost_basis_count == 0
        )
        known_total = (
            cash_total + priced_positions_value if cash_total is not None else None
        )
        total_value = known_total if valuation_complete else None
        aggregate_cost_basis = positions_cost_basis if all_cost_basis_known else None
        aggregate_unrealized = (
            priced_positions_value - positions_cost_basis
            if all_positions_priced and all_cost_basis_known
            else None
        )
        aggregate_unrealized_percentage = (
            aggregate_unrealized / positions_cost_basis * Decimal("100")
            if aggregate_unrealized is not None and positions_cost_basis > 0
            else None
        )
        portfolio = PortfolioSnapshot(
            analysis_run_id=analysis_run.id,
            pipeline_run_id=pipeline_run_id,
            user_id=user.id,
            exchange=exchange,
            quote_asset=quote_asset,
            valuation_policy_signature=(
                self.settings.portfolio_valuation_policy_signature
            ),
            cash_available_krw=cash_available,
            cash_locked_krw=cash_locked,
            cash_total_krw=cash_total,
            priced_positions_value_krw=priced_positions_value,
            known_total_value_krw=known_total,
            total_value_krw=total_value,
            positions_estimated_cost_basis_krw=aggregate_cost_basis,
            unrealized_pnl_krw=aggregate_unrealized,
            unrealized_pnl_percentage=aggregate_unrealized_percentage,
            position_count=len(position_values),
            unpriced_asset_count=unpriced_count,
            missing_cost_basis_count=missing_cost_basis_count,
            valuation_status="COMPLETE" if valuation_complete else "PARTIAL",
            captured_at=self.now_fn(),
        )
        self.session.add(portfolio)
        self.session.flush()
        positions = tuple(
            PortfolioPositionSnapshot(
                portfolio_snapshot_id=portfolio.id,
                **values,
            )
            for values in position_values
        )
        self.session.add_all(positions)
        self.session.flush()
        return PortfolioValuationResult(
            analysis_run=analysis_run,
            portfolio_snapshot=portfolio,
            positions=positions,
            already_captured=False,
        )

    def _get_coingecko_prices(self, assets: list[str]) -> dict[str, Decimal]:
        if not self.settings.coingecko_enabled:
            return {}
        identity_map = self.settings.portfolio_coingecko_asset_identity_map
        requested = {
            asset: identity_map[asset]
            for asset in sorted(set(assets))
            if asset in identity_map
        }
        if not requested:
            return {}
        try:
            rows = self.coingecko_client.get_markets(
                list(dict.fromkeys(requested.values())), vs_currency="krw"
            )
        except httpx.HTTPError, ValueError:
            return {}
        price_by_id: dict[str, Decimal] = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            price = self._positive_decimal(row.get("current_price"))
            if price is not None:
                price_by_id[row["id"]] = price
        return {
            asset: price_by_id[coin_id]
            for asset, coin_id in requested.items()
            if coin_id in price_by_id
        }

    def _get_existing(
        self, user_id: int, exchange: str, pipeline_run_id: str
    ) -> PortfolioValuationResult | None:
        snapshot = self.session.scalar(
            select(PortfolioSnapshot).where(
                PortfolioSnapshot.user_id == user_id,
                PortfolioSnapshot.exchange == exchange,
                PortfolioSnapshot.pipeline_run_id == pipeline_run_id,
            )
        )
        if snapshot is None:
            return None
        analysis_run = self.session.get(AnalysisRun, snapshot.analysis_run_id)
        if analysis_run is None or analysis_run.status != "SUCCESS":
            raise ValueError("Existing portfolio snapshot has invalid analysis run")
        positions = tuple(
            self.session.scalars(
                select(PortfolioPositionSnapshot)
                .where(PortfolioPositionSnapshot.portfolio_snapshot_id == snapshot.id)
                .order_by(PortfolioPositionSnapshot.currency)
            )
        )
        return PortfolioValuationResult(
            analysis_run=analysis_run,
            portfolio_snapshot=snapshot,
            positions=positions,
            already_captured=True,
        )

    def _get_account_run_id(
        self, user_id: int, exchange: str, pipeline_run_id: str
    ) -> int | None:
        return self.session.scalar(
            select(AnalysisRun.id)
            .join(
                AccountSnapshot,
                AccountSnapshot.analysis_run_id == AnalysisRun.id,
            )
            .where(
                AnalysisRun.user_id == user_id,
                AnalysisRun.pipeline_run_id == pipeline_run_id,
                AnalysisRun.run_type.in_(ACCOUNT_SNAPSHOT_RUN_TYPES),
                AnalysisRun.status == "SUCCESS",
                AccountSnapshot.user_id == user_id,
                AccountSnapshot.exchange == exchange,
            )
            .order_by(
                case((AnalysisRun.run_type == "ACCOUNT_SNAPSHOT", 0), else_=1),
                AnalysisRun.id.desc(),
            )
            .limit(1)
        )

    def _get_universe_run_id(self, user_id: int, pipeline_run_id: str) -> int | None:
        return self.session.scalar(
            select(AnalysisRun.id)
            .where(
                AnalysisRun.user_id == user_id,
                AnalysisRun.pipeline_run_id == pipeline_run_id,
                AnalysisRun.run_type == "MARKET_UNIVERSE",
                AnalysisRun.status == "SUCCESS",
            )
            .order_by(AnalysisRun.id.desc())
            .limit(1)
        )

    @staticmethod
    def _non_negative_decimal(value: object) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = Decimal(str(value).strip())
        except InvalidOperation, TypeError, ValueError:
            return None
        return parsed if parsed.is_finite() and parsed >= 0 else None

    @classmethod
    def _positive_decimal(cls, value: object) -> Decimal | None:
        parsed = cls._non_negative_decimal(value)
        return parsed if parsed is not None and parsed > 0 else None
