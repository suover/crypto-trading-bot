import argparse

from sqlalchemy import select

from crypto_trading_bot.db.models import PortfolioPerformanceSnapshot, PortfolioSnapshot
from crypto_trading_bot.services.cash_flow_valuation_service import (
    CashFlowValuationService,
)
from crypto_trading_bot.services.portfolio_performance_service import (
    PortfolioPerformanceResult,
    PortfolioPerformanceService,
)


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild external-flow valuations and account-level portfolio performance. "
            "The default is a read-only dry-run."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--user-id", type=int, default=None)
    namespace = parser.parse_args(args)
    if namespace.user_id is not None and namespace.user_id <= 0:
        parser.error("--user-id must be greater than 0")
    return namespace


def _scopes(session, user_id: int | None) -> tuple[tuple[int, str], ...]:
    source = select(PortfolioSnapshot.user_id, PortfolioSnapshot.exchange)
    existing = select(
        PortfolioPerformanceSnapshot.user_id, PortfolioPerformanceSnapshot.exchange
    )
    if user_id is not None:
        source = source.where(PortfolioSnapshot.user_id == user_id)
        existing = existing.where(PortfolioPerformanceSnapshot.user_id == user_id)
    return tuple(sorted(set(session.execute(source)) | set(session.execute(existing))))


def _print_result(cash_result, result: PortfolioPerformanceResult) -> None:
    complete_nav_count = sum(plan.end_value_krw is not None for plan in result.plans)
    latest = result.plans[-1] if result.plans else None
    print(f"portfolio_snapshot_count={len(result.plans)}")
    print(f"complete_nav_count={complete_nav_count}")
    print(f"partial_nav_count={len(result.plans) - complete_nav_count}")
    print(f"cash_flow_activity_count={len(cash_result.plans)}")
    print(f"cash_flow_complete_count={cash_result.complete_count}")
    print(f"cash_flow_partial_count={cash_result.partial_count}")
    print(f"performance_new_count={result.new_count}")
    print(f"performance_update_count={result.update_count}")
    print(
        "latest_period_return_percentage="
        f"{latest.period_return_percentage if latest else None}"
    )
    print(
        "cumulative_return_percentage="
        f"{latest.cumulative_return_percentage if latest else None}"
    )
    print(
        f"max_drawdown_percentage={latest.max_drawdown_percentage if latest else None}"
    )
    print(f"performance_status={latest.performance_status if latest else 'NO_DATA'}")


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        with SessionLocal() as session:
            scopes = _scopes(session, namespace.user_id)
            print(f"mode={'APPLY' if namespace.apply else 'DRY_RUN'}")
            print(f"scope_count={len(scopes)}")
            for user_id, exchange in scopes:
                print(f"user_id={user_id}")
                print(f"exchange={exchange}")
                cash_result = CashFlowValuationService(session).value_scope(
                    user_id, exchange=exchange, apply=namespace.apply
                )
                result = PortfolioPerformanceService(session).rebuild(
                    user_id,
                    exchange=exchange,
                    valuation_plans=cash_result.plans,
                    apply=namespace.apply,
                )
                _print_result(cash_result, result)
            if namespace.apply:
                session.commit()
            else:
                session.rollback()
    except Exception as error:
        print(
            f"Portfolio performance rebuild failed. error_type={type(error).__name__}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
