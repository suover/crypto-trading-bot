from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.services.account_snapshot_service import AccountSnapshotService
from crypto_trading_bot.services.runtime_user_resolver import RuntimeUserResolver


def collect_account_snapshots() -> None:
    with SessionLocal() as session:
        user = RuntimeUserResolver(session).resolve_configured(
            get_settings().trading_user_id
        )
        service = AccountSnapshotService(session)
        analysis_run, snapshots = service.collect_account_snapshots(user.id)

        print(
            f"Account snapshots saved. "
            f"analysis_run_id={analysis_run.id}, "
            f"snapshot_count={len(snapshots)}"
        )

        for snapshot in snapshots:
            print(
                f"{snapshot.currency} | "
                f"balance={snapshot.balance} | "
                f"locked={snapshot.locked} | "
                f"avg_buy_price={snapshot.avg_buy_price}"
            )


if __name__ == "__main__":
    from crypto_trading_bot.operational.error_reporting import (
        run_with_operational_error_reporting,
    )

    run_with_operational_error_reporting(
        collect_account_snapshots, service_hint="UPBIT"
    )
