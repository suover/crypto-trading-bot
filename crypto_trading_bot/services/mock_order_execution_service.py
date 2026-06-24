from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import (
    ApprovalRequest,
    OrderLog,
    TradeRecommendation,
    User,
)
from crypto_trading_bot.exchange.upbit_client import UpbitClient


KST = ZoneInfo("Asia/Seoul")

MIN_ORDER_AMOUNT_KRW = Decimal("5000")
MONEY_QUANTUM = Decimal("0.01")
PRICE_QUANTUM = Decimal("0.0000000001")
QUANTITY_QUANTUM = Decimal("0.0000000001")

MOCK_ORDER_STATUS = "MOCK_FILLED"


class MockOrderExecutionError(ValueError):
    """모의 주문을 안전하게 실행할 수 없을 때 발생하는 예외."""


@dataclass(frozen=True)
class MockOrderExecutionResult:
    order_log: OrderLog
    recommendation: TradeRecommendation
    already_executed: bool


class MockOrderExecutionService:
    def __init__(
        self,
        session: Session,
        upbit_client: UpbitClient | None = None,
    ) -> None:
        self.session = session
        self.upbit_client = upbit_client or UpbitClient()

    def execute(
        self,
        recommendation_id: int,
        approval_request_id: int,
        commit: bool = True,
    ) -> MockOrderExecutionResult:
        settings = get_settings()

        recommendation = self._get_recommendation_for_update(
            recommendation_id=recommendation_id,
        )

        if recommendation is None:
            raise MockOrderExecutionError(
                "Trade recommendation not found. "
                f"recommendation_id={recommendation_id}"
            )

        approval_request = self._get_approval_request_for_update(
            approval_request_id=approval_request_id,
        )

        if approval_request is None:
            raise MockOrderExecutionError(
                "Approval request not found. "
                f"approval_request_id={approval_request_id}"
            )

        # 기존 주문 여부를 확인하기 전에 승인 요청과 추천의 관계부터 검증
        self._validate_approval_request(
            recommendation=recommendation,
            approval_request=approval_request,
        )

        # 동일 추천으로 이미 생성된 주문이 있으면 새로 만들지 않고 반환
        existing_order_log = self._get_order_log(
            recommendation_id=recommendation.id,
        )

        if existing_order_log is not None:
            if existing_order_log.approval_request_id != approval_request.id:
                raise MockOrderExecutionError(
                    "Existing order approval request does not match. "
                    f"existing_approval_request_id="
                    f"{existing_order_log.approval_request_id}, "
                    f"requested_approval_request_id={approval_request.id}"
                )

            return MockOrderExecutionResult(
                order_log=existing_order_log,
                recommendation=recommendation,
                already_executed=True,
            )

        # 신규 주문을 만들 때만 추천 상태가 APPROVED인지 검증
        self._validate_recommendation_status(
            recommendation=recommendation,
        )

        # 동일 사용자의 주문을 순차 처리하여 일일 한도 동시성 문제 방지
        user = self._get_user_for_update(user_id=recommendation.user_id)

        if user is None:
            raise MockOrderExecutionError(
                f"User not found. user_id={recommendation.user_id}"
            )

        self._validate_recommendation(
            recommendation=recommendation,
            allowed_markets=settings.allowed_market_list,
        )

        current_price = self._get_current_price(
            market=recommendation.market,
        )

        accounts = self.upbit_client.get_accounts()

        if recommendation.action == "BUY":
            amount_krw, quantity = self._prepare_buy_order(
                recommendation=recommendation,
                accounts=accounts,
                current_price=current_price,
                max_order_amount_krw=Decimal(
                    settings.max_order_amount_krw
                ),
            )
        else:
            amount_krw, quantity = self._prepare_sell_order(
                recommendation=recommendation,
                accounts=accounts,
                current_price=current_price,
                max_order_amount_krw=Decimal(
                    settings.max_order_amount_krw
                ),
            )

        daily_order_amount = self._get_daily_mock_order_amount(
            user_id=recommendation.user_id,
        )

        daily_max_order_amount = Decimal(
            settings.daily_max_order_amount_krw
        )

        if daily_order_amount + amount_krw > daily_max_order_amount:
            raise MockOrderExecutionError(
                "Daily order amount limit exceeded. "
                f"current_daily_amount={daily_order_amount}, "
                f"requested_amount={amount_krw}, "
                f"daily_limit={daily_max_order_amount}"
            )

        order_log = OrderLog(
            recommendation_id=recommendation.id,
            approval_request_id=approval_request.id,
            user_id=recommendation.user_id,
            trading_mode="MOCK",
            exchange=recommendation.exchange,
            market=recommendation.market,
            side=recommendation.action,
            order_type="MARKET",
            amount_krw=amount_krw,
            quantity=quantity,
            price=current_price,
            status=MOCK_ORDER_STATUS,
            exchange_order_id=None,
            error_message=None,
            raw_response={
                "simulated": True,
                "actual_order_executed": False,
                "recommendation_id": recommendation.id,
                "approval_request_id": approval_request.id,
                "market": recommendation.market,
                "side": recommendation.action,
                "amount_krw": str(amount_krw),
                "quantity": str(quantity),
                "price": str(current_price),
                "daily_order_amount_before": str(daily_order_amount),
                "daily_order_amount_after": str(
                    daily_order_amount + amount_krw
                ),
            },
        )

        recommendation.status = "MOCK_EXECUTED"

        self.session.add(order_log)
        self.session.flush()

        if commit:
            self.session.commit()
            self.session.refresh(order_log)
            self.session.refresh(recommendation)

        return MockOrderExecutionResult(
            order_log=order_log,
            recommendation=recommendation,
            already_executed=False,
        )

    def _get_recommendation_for_update(
        self,
        recommendation_id: int,
    ) -> TradeRecommendation | None:
        statement = (
            select(TradeRecommendation)
            .where(TradeRecommendation.id == recommendation_id)
            .with_for_update()
        )

        return self.session.scalar(statement)

    def _get_approval_request_for_update(
        self,
        approval_request_id: int,
    ) -> ApprovalRequest | None:
        statement = (
            select(ApprovalRequest)
            .where(ApprovalRequest.id == approval_request_id)
            .with_for_update()
        )

        return self.session.scalar(statement)

    def _get_user_for_update(
        self,
        user_id: int,
    ) -> User | None:
        statement = (
            select(User)
            .where(User.id == user_id)
            .with_for_update()
        )

        return self.session.scalar(statement)

    def _get_order_log(
        self,
        recommendation_id: int,
    ) -> OrderLog | None:
        statement = select(OrderLog).where(
            OrderLog.recommendation_id == recommendation_id
        )

        return self.session.scalar(statement)

    @staticmethod
    def _validate_approval_request(
        recommendation: TradeRecommendation,
        approval_request: ApprovalRequest,
    ) -> None:
        if approval_request.recommendation_id != recommendation.id:
            raise MockOrderExecutionError(
                "Approval request does not belong to recommendation"
            )

        if approval_request.user_id != recommendation.user_id:
            raise MockOrderExecutionError(
                "Approval request user does not match recommendation user"
            )

        if approval_request.status != "APPROVED":
            raise MockOrderExecutionError(
                "Approval request is not approved. "
                f"status={approval_request.status}"
            )

        if approval_request.approved_at is None:
            raise MockOrderExecutionError(
                "Approval request does not have approved_at"
            )

    @staticmethod
    def _validate_recommendation_status(
        recommendation: TradeRecommendation,
    ) -> None:
        if recommendation.status != "APPROVED":
            raise MockOrderExecutionError(
                "Trade recommendation is not approved. "
                f"status={recommendation.status}"
            )

    @staticmethod
    def _validate_recommendation(
        recommendation: TradeRecommendation,
        allowed_markets: list[str],
    ) -> None:
        if recommendation.exchange != "UPBIT":
            raise MockOrderExecutionError(
                "Unsupported exchange. "
                f"exchange={recommendation.exchange}"
            )

        if recommendation.market not in allowed_markets:
            raise MockOrderExecutionError(
                "Market is not allowed. "
                f"market={recommendation.market}"
            )

        if recommendation.action not in {"BUY", "SELL"}:
            raise MockOrderExecutionError(
                "Only BUY or SELL recommendations can be executed. "
                f"action={recommendation.action}"
            )

    def _get_current_price(
        self,
        market: str,
    ) -> Decimal:
        tickers = self.upbit_client.get_tickers([market])

        for ticker in tickers:
            if ticker.get("market") != market:
                continue

            current_price = self._to_decimal(
                ticker.get("trade_price")
            )

            if current_price <= 0:
                break

            return current_price.quantize(
                PRICE_QUANTUM,
                rounding=ROUND_DOWN,
            )

        raise MockOrderExecutionError(
            f"Current market price was not found. market={market}"
        )

    def _prepare_buy_order(
        self,
        recommendation: TradeRecommendation,
        accounts: list[dict[str, object]],
        current_price: Decimal,
        max_order_amount_krw: Decimal,
    ) -> tuple[Decimal, Decimal]:
        amount_krw = self._to_decimal(
            recommendation.recommended_amount_krw
        ).quantize(
            MONEY_QUANTUM,
            rounding=ROUND_DOWN,
        )

        if amount_krw < MIN_ORDER_AMOUNT_KRW:
            raise MockOrderExecutionError(
                "Buy amount is below minimum order amount. "
                f"amount={amount_krw}"
            )

        if amount_krw > max_order_amount_krw:
            raise MockOrderExecutionError(
                "Buy amount exceeds maximum order amount. "
                f"amount={amount_krw}, "
                f"maximum={max_order_amount_krw}"
            )

        krw_balance = self._get_account_balance(
            accounts=accounts,
            currency="KRW",
        )

        if amount_krw > krw_balance:
            raise MockOrderExecutionError(
                "Insufficient KRW balance. "
                f"balance={krw_balance}, "
                f"required={amount_krw}"
            )

        quantity = (amount_krw / current_price).quantize(
            QUANTITY_QUANTUM,
            rounding=ROUND_DOWN,
        )

        if quantity <= 0:
            raise MockOrderExecutionError(
                "Calculated buy quantity must be greater than 0"
            )

        return amount_krw, quantity

    def _prepare_sell_order(
        self,
        recommendation: TradeRecommendation,
        accounts: list[dict[str, object]],
        current_price: Decimal,
        max_order_amount_krw: Decimal,
    ) -> tuple[Decimal, Decimal]:
        quantity = self._to_decimal(
            recommendation.recommended_quantity
        ).quantize(
            QUANTITY_QUANTUM,
            rounding=ROUND_DOWN,
        )

        if quantity <= 0:
            raise MockOrderExecutionError(
                "Sell quantity must be greater than 0"
            )

        base_currency = self._get_base_currency(
            market=recommendation.market,
        )

        coin_balance = self._get_account_balance(
            accounts=accounts,
            currency=base_currency,
        )

        if quantity > coin_balance:
            raise MockOrderExecutionError(
                "Insufficient coin balance. "
                f"currency={base_currency}, "
                f"balance={coin_balance}, "
                f"required={quantity}"
            )

        amount_krw = (quantity * current_price).quantize(
            MONEY_QUANTUM,
            rounding=ROUND_DOWN,
        )

        if amount_krw < MIN_ORDER_AMOUNT_KRW:
            raise MockOrderExecutionError(
                "Sell amount is below minimum order amount. "
                f"amount={amount_krw}"
            )

        if amount_krw > max_order_amount_krw:
            raise MockOrderExecutionError(
                "Sell amount exceeds maximum order amount. "
                f"amount={amount_krw}, "
                f"maximum={max_order_amount_krw}"
            )

        return amount_krw, quantity

    def _get_daily_mock_order_amount(
        self,
        user_id: int,
    ) -> Decimal:
        now = datetime.now(KST)
        day_start = now.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        next_day_start = day_start + timedelta(days=1)

        statement = select(
            func.coalesce(
                func.sum(OrderLog.amount_krw),
                0,
            )
        ).where(
            OrderLog.user_id == user_id,
            OrderLog.status == MOCK_ORDER_STATUS,
            OrderLog.created_at >= day_start,
            OrderLog.created_at < next_day_start,
        )

        return self._to_decimal(self.session.scalar(statement))

    @staticmethod
    def _get_account_balance(
        accounts: list[dict[str, object]],
        currency: str,
    ) -> Decimal:
        normalized_currency = currency.strip().upper()

        for account in accounts:
            account_currency = str(
                account.get("currency", "")
            ).strip().upper()

            if account_currency != normalized_currency:
                continue

            return MockOrderExecutionService._to_decimal(
                account.get("balance")
            )

        return Decimal("0")

    @staticmethod
    def _get_base_currency(
        market: str,
    ) -> str:
        parts = market.split("-")

        if len(parts) != 2 or not parts[1]:
            raise MockOrderExecutionError(
                f"Unexpected market format. market={market}"
            )

        return parts[1].upper()

    @staticmethod
    def _to_decimal(
        value: object | None,
    ) -> Decimal:
        if value is None:
            return Decimal("0")

        try:
            return Decimal(str(value))
        except Exception as error:
            raise MockOrderExecutionError(
                f"Invalid decimal value. value={value}"
            ) from error