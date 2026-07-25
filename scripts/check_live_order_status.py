from argparse import ArgumentParser

from crypto_trading_bot.db.database import SessionLocal
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
    except LiveOrderReconciliationError as error:
        print(f"recommendation_id={namespace.recommendation_id}")
        print("result=UNRESOLVED")
        print(f"error_type={type(error).__name__}")
        return 1

    response = result.upbit_response
    print(f"recommendation_id={result.recommendation.id}")
    print(f"market={result.order_log.market}")
    print(f"identifier={result.identifier}")
    print(f"exchange_order_id={result.order_log.exchange_order_id or '-'}")
    print(f"upbit_state={result.upbit_state or '-'}")
    print(f"local_status={result.order_log.status}")
    print(f"executed_volume={response.get('executed_volume', '-')}")
    print(f"paid_fee={response.get('paid_fee', '-')}")
    print("result=RESOLVED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
