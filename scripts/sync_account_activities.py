import argparse
from datetime import datetime

from sqlalchemy import select

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import User
from crypto_trading_bot.services.account_activity_sync_service import (
    AccountActivitySyncService,
)


def parse_timestamp(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timestamp must be ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read Upbit account activity. Dry-run unless --apply is supplied."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--user-name", default="Minsu")
    parser.add_argument("--start-at", type=parse_timestamp)
    parser.add_argument("--end-at", type=parse_timestamp)
    return parser.parse_args(args)


def run(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.name == namespace.user_name))
        if user is None:
            print("Account activity sync failed. error_type=UserNotFound")
            return 1
        result = AccountActivitySyncService(session).run(
            user.id,
            start_at=namespace.start_at,
            end_at=namespace.end_at,
            apply=namespace.apply,
        )
    print(f"mode={'APPLY' if namespace.apply else 'DRY_RUN'}")
    for source in result.sources:
        print("")
        print(f"source={source.source_type}")
        print(f"status={source.status}")
        print(f"fetched_count={source.fetched_count}")
        print(f"new_count={source.new_count}")
        print(f"update_count={source.update_count}")
        if source.source_type == "UPBIT_CLOSED_ORDER":
            print(f"bot_order_count={source.bot_order_count}")
            print(f"external_order_count={source.external_order_count}")
        print(f"coverage_start_at={source.coverage_start_at.isoformat()}")
        print(f"coverage_end_at={source.coverage_end_at.isoformat()}")
        if source.safe_error_code:
            print(f"safe_error_code={source.safe_error_code}")
    print("")
    print(f"cash_flow_in_count={result.cash_flow_in_count}")
    print(f"cash_flow_out_count={result.cash_flow_out_count}")
    return 0 if result.complete else 1


def main(args: list[str] | None = None) -> int:
    try:
        return run(args)
    except Exception as error:
        print(f"Account activity sync failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
