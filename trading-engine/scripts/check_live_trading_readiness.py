from argparse import ArgumentParser, Namespace

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.services.live_trading_readiness import (
    LiveTradingReadinessReport,
    LiveTradingReadinessService,
)


def parse_args() -> Namespace:
    parser = ArgumentParser(
        description=(
            "Check whether the application is ready for live Upbit trading. "
            "No actual Upbit order is placed."
        ),
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with an error when live trading readiness is not ready.",
    )

    return parser.parse_args()


def print_report(report: LiveTradingReadinessReport) -> None:
    print("Live trading readiness check")
    print("=" * 60)
    print("Actual Upbit order will not be executed.")
    print("=" * 60)
    print(f"overall_ready={report.ready}")
    print()

    for item in report.items:
        status = "OK" if item.ready else "FAIL"
        print(f"[{status}] {item.name} - {item.message}")


def main(strict: bool) -> None:
    with SessionLocal() as session:
        service = LiveTradingReadinessService(
            session=session,
        )
        report = service.check()

    print_report(report)

    if strict and not report.ready:
        raise SystemExit(1)


def run() -> None:
    args = parse_args()

    main(
        strict=args.strict,
    )


if __name__ == "__main__":
    run()
