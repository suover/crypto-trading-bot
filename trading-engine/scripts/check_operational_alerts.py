from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select

from crypto_trading_bot.db.models import OperationalAlert
from crypto_trading_bot.services.operational_alert_service import (
    OperationalAlertService,
)


KST = ZoneInfo("Asia/Seoul")


def main() -> int:
    try:
        from crypto_trading_bot.config.settings import get_settings
        from crypto_trading_bot.db.database import SessionLocal

        settings = get_settings()
        now = datetime.now(KST)
        with SessionLocal() as session:
            stale = OperationalAlertService(session).find_stale_live_orders(
                stale_after_seconds=settings.live_order_stale_alert_after_seconds,
                now=now,
            )
            with session.no_autoflush:
                pending_count = session.scalar(
                    select(func.count(OperationalAlert.id)).where(
                        OperationalAlert.delivery_status == "PENDING"
                    )
                )
                retryable_count = session.scalar(
                    select(func.count(OperationalAlert.id)).where(
                        and_(
                            OperationalAlert.delivery_status == "FAILED",
                            OperationalAlert.next_retry_at.is_not(None),
                            OperationalAlert.next_retry_at <= now,
                            OperationalAlert.delivery_attempt_count
                            < 1 + settings.operational_alert_max_retries,
                        )
                    )
                )
            print("mode=DRY_RUN")
            print(f"stale_live_order_count={len(stale)}")
            print(f"pending_alert_count={pending_count or 0}")
            print(f"retryable_alert_count={retryable_count or 0}")
            for candidate in stale:
                print(
                    f"order_log_id={candidate.order_log_id} "
                    f"recommendation_id={candidate.recommendation_id} "
                    f"market={candidate.market} side={candidate.side} "
                    f"status={candidate.status} age_seconds={candidate.age_seconds}"
                )
            session.rollback()
    except Exception as error:
        print(f"Operational alert diagnostic failed. error_type={type(error).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
