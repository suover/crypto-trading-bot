from decimal import Decimal

from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import Settings, get_settings
from crypto_trading_bot.db.models import Exchange, ExchangeMarket


class ExchangeMarketRegistryService:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()

    def load_active_markets(self) -> list[ExchangeMarket]:
        return (
            self.session.query(ExchangeMarket)
            .join(Exchange, Exchange.code == ExchangeMarket.exchange_code)
            .filter(
                Exchange.enabled.is_(True),
                ExchangeMarket.status == "ACTIVE",
            )
            .order_by(
                ExchangeMarket.priority,
                ExchangeMarket.exchange_code,
                ExchangeMarket.market,
            )
            .all()
        )

    def load_active_markets_for_exchange(
        self,
        exchange_code: str,
    ) -> list[ExchangeMarket]:
        return (
            self.session.query(ExchangeMarket)
            .join(Exchange, Exchange.code == ExchangeMarket.exchange_code)
            .filter(
                Exchange.code == exchange_code,
                Exchange.enabled.is_(True),
                ExchangeMarket.status == "ACTIVE",
            )
            .order_by(ExchangeMarket.priority, ExchangeMarket.market)
            .all()
        )

    def load_allowed_active_markets_for_exchange(
        self,
        exchange_code: str,
    ) -> list[ExchangeMarket]:
        allowed_markets = self.settings.allowed_market_list
        if not allowed_markets:
            raise ValueError(
                f"Allowed markets must not be empty. exchange={exchange_code}"
            )

        active_markets = self.load_active_markets_for_exchange(exchange_code)
        active_market_by_name = {
            exchange_market.market: exchange_market
            for exchange_market in active_markets
        }
        unavailable_markets = [
            market for market in allowed_markets if market not in active_market_by_name
        ]
        if unavailable_markets:
            raise ValueError(
                "Allowed markets are missing or inactive for exchange. "
                f"exchange={exchange_code}, markets={unavailable_markets}"
            )

        return [active_market_by_name[market] for market in allowed_markets]

    def get_market(
        self,
        exchange_code: str,
        market: str,
    ) -> ExchangeMarket | None:
        return (
            self.session.query(ExchangeMarket)
            .filter(
                ExchangeMarket.exchange_code == exchange_code,
                ExchangeMarket.market == market,
            )
            .first()
        )

    def calculate_final_max_order_amount(
        self,
        exchange_market: ExchangeMarket,
    ) -> Decimal:
        exchange = self._load_exchange(exchange_market.exchange_code)
        ceiling = Decimal(str(self.settings.max_order_amount_krw))
        inherited_amount = (
            exchange_market.max_order_amount_override
            if exchange_market.max_order_amount_override is not None
            else exchange.default_max_order_amount
        )
        amount = (
            Decimal(str(inherited_amount)) if inherited_amount is not None else ceiling
        )
        return min(amount, ceiling)

    def calculate_default_max_order_amount(self, exchange_code: str) -> Decimal:
        exchange = self._load_exchange(exchange_code)
        ceiling = Decimal(str(self.settings.max_order_amount_krw))
        if exchange.default_max_order_amount is None:
            return ceiling
        return min(Decimal(str(exchange.default_max_order_amount)), ceiling)

    def calculate_final_daily_max_order_amount(
        self,
        exchange_market: ExchangeMarket,
    ) -> Decimal:
        exchange = self._load_exchange(exchange_market.exchange_code)
        ceiling = Decimal(str(self.settings.daily_max_order_amount_krw))
        inherited_amount = (
            exchange_market.daily_max_order_amount_override
            if exchange_market.daily_max_order_amount_override is not None
            else exchange.default_daily_max_order_amount
        )
        amount = (
            Decimal(str(inherited_amount)) if inherited_amount is not None else ceiling
        )
        return min(amount, ceiling)

    def _load_exchange(self, exchange_code: str) -> Exchange:
        exchange = (
            self.session.query(Exchange).filter(Exchange.code == exchange_code).first()
        )
        if exchange is None:
            raise ValueError(f"Exchange not found. code={exchange_code}")
        return exchange
