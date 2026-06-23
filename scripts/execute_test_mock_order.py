import argparse
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import ApprovalRequest
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.services.mock_order_execution_service import (
    MockOrderExecutionService,
)


TEST_CURRENT_PRICE = 100_000_000
TEST_KRW_BALANCE = "100000"
TEST_BTC_BALANCE = "0.01"


class TestUpbitClient(UpbitClient):
    """실제 주문이나 실제 잔고를 사용하지 않는 모의 주문 테스트 클라이언트."""

    def get_tickers(
        self,
        markets: list[str],
    ) -> list[dict[str, Any]]:
        return [
            {
                "market": market,
                "trade_price": TEST_CURRENT_PRICE,
            }
            for market in markets
        ]

    def get_accounts(self) -> list[dict[str, Any]]:
        return [
            {
                "currency": "KRW",
                "balance": TEST_KRW_BALANCE,
                "locked": "0",
                "avg_buy_price": "0",
                "unit_currency": "KRW",
            },
            {
                "currency": "BTC",
                "balance": TEST_BTC_BALANCE,
                "locked": "0",
                "avg_buy_price": "90000000",
                "unit_currency": "KRW",
            },
        ]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Execute one approved recommendation as a mock order. "
            "No actual Upbit order is placed."
        )
    )

    parser.add_argument(
        "--recommendation-id",
        type=int,
        required=True,
        help="Approved trade recommendation ID",
    )

    return parser.parse_args()


def get_approved_request(
    session: Session,
    recommendation_id: int,
) -> ApprovalRequest:
    statement = (
        select(ApprovalRequest)
        .where(
            ApprovalRequest.recommendation_id == recommendation_id,
            ApprovalRequest.status == "APPROVED",
        )
        .order_by(ApprovalRequest.id.desc())
        .limit(1)
    )

    approval_request = session.scalar(statement)

    if approval_request is None:
        raise ValueError(
            "Approved request not found. "
            f"recommendation_id={recommendation_id}"
        )

    return approval_request


def execute_test_mock_order(
    recommendation_id: int,
) -> None:
    with SessionLocal() as session:
        approval_request = get_approved_request(
            session=session,
            recommendation_id=recommendation_id,
        )

        service = MockOrderExecutionService(
            session=session,
            upbit_client=TestUpbitClient(),
        )

        result = service.execute(
            recommendation_id=recommendation_id,
            approval_request_id=approval_request.id,
        )

        print("Mock order execution completed.")
        print(f"order_log_id={result.order_log.id}")
        print(
            f"recommendation_id="
            f"{result.order_log.recommendation_id}"
        )
        print(
            f"approval_request_id="
            f"{result.order_log.approval_request_id}"
        )
        print(f"market={result.order_log.market}")
        print(f"side={result.order_log.side}")
        print(f"amount_krw={result.order_log.amount_krw}")
        print(f"quantity={result.order_log.quantity}")
        print(f"price={result.order_log.price}")
        print(f"status={result.order_log.status}")
        print(f"already_executed={result.already_executed}")
        print("Test data source: TestUpbitClient")
        print("Actual Upbit order was not executed.")


if __name__ == "__main__":
    arguments = parse_arguments()

    execute_test_mock_order(
        recommendation_id=arguments.recommendation_id,
    )