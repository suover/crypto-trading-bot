from argparse import ArgumentParser

from sqlalchemy import func, select

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import OrderFill
from crypto_trading_bot.services.live_order_reconciliation_service import (
    LiveOrderReconciliationError,
    LiveOrderReconciliationService,
)


def parse_args(args: list[str] | None = None):
    parser = ArgumentParser(description="Reconcile an existing LIVE Upbit order.")
    parser.add_argument("--recommendation-id", type=int, required=True)
    return parser.parse_args(args)


def main(args: list[str] | None = None) -> int:
    namespace = parse_args(args)
    try:
        with SessionLocal() as session:
            result = LiveOrderReconciliationService(session).reconcile(
                namespace.recommendation_id
            )
            normalized_fill_count = session.scalar(
                select(func.count(OrderFill.id)).where(
                    OrderFill.order_log_id == result.order_log.id
                )
            )
    except LiveOrderReconciliationError as error:
        print(f"recommendation_id={namespace.recommendation_id}")
        print("result=UNRESOLVED")
        print(f"error_type={type(error).__name__}")
        return 1

    print(f"recommendation_id={result.recommendation.id}")
    print(f"market={result.order_log.market}")
    print(f"identifier={result.identifier}")
    print(f"exchange_order_id={result.order_log.exchange_order_id or '-'}")
    print(f"upbit_state={result.upbit_state or '-'}")
    print(f"local_status={result.order_log.status}")
    print(f"executed_quantity={result.order_log.executed_quantity}")
    print(f"executed_funds_krw={result.order_log.executed_funds_krw}")
    print(f"average_execution_price={result.order_log.average_execution_price}")
    print(f"paid_fee={result.order_log.paid_fee}")
    print(f"remaining_quantity={result.order_log.remaining_quantity}")
    print(f"trades_count={result.order_log.trades_count}")
    print(f"execution_synced_at={result.order_log.execution_synced_at}")
    print(f"normalized_fill_count={normalized_fill_count or 0}")
    print("result=RESOLVED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
