from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.services.market_snapshot_service import MarketSnapshotService


def collect_market_snapshots() -> None:
    with SessionLocal() as session:
        service = MarketSnapshotService(session)

        analysis_run, snapshots = service.collect_market_snapshots()

        print(
            f"Analysis run saved. id={analysis_run.id}, snapshot_count={len(snapshots)}"
        )

        for snapshot in snapshots:
            print(
                f"{snapshot.market} | "
                f"price={snapshot.current_price} | "
                f"change_rate={snapshot.change_rate}"
            )


if __name__ == "__main__":
    collect_market_snapshots()
