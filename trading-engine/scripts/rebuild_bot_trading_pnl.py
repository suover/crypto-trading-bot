import argparse

from sqlalchemy import select

from crypto_trading_bot.db.models import BotTradingPnlSummary, OrderLog
from crypto_trading_bot.services.bot_trading_pnl_service import (
    TERMINAL_PNL_SOURCE_STATUSES,
    BotTradingPnlCalculation,
    BotTradingPnlService,
)


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild DB-only bot FIFO realized PnL from normalized LIVE executions. "
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
    source = select(OrderLog.user_id, OrderLog.exchange).where(
        OrderLog.trading_mode == "LIVE",
        OrderLog.exchange == "UPBIT",
        OrderLog.status.in_(TERMINAL_PNL_SOURCE_STATUSES),
    )
    existing = select(
        BotTradingPnlSummary.user_id, BotTradingPnlSummary.exchange
    ).where(BotTradingPnlSummary.exchange == "UPBIT")
    if user_id is not None:
        source = source.where(OrderLog.user_id == user_id)
        existing = existing.where(BotTradingPnlSummary.user_id == user_id)
    return tuple(sorted(set(session.execute(source)) | set(session.execute(existing))))


def _print_result(calculation: BotTradingPnlCalculation) -> None:
    summary = calculation.summary
    print(f"user_id={summary.user_id}")
    print(f"exchange={summary.exchange}")
    print(f"source_order_count={summary.source_order_count}")
    print(f"valid_buy_count={summary.processed_buy_order_count}")
    print(f"valid_sell_count={summary.processed_sell_order_count}")
    print(f"fully_matched_sell_count={summary.fully_matched_sell_count}")
    print(f"partially_matched_sell_count={summary.partially_matched_sell_count}")
    print(f"unmatched_sell_count={summary.unmatched_sell_count}")
    print(f"incomplete_order_count={summary.incomplete_order_count}")
    print(f"recognized_realized_pnl_krw={summary.recognized_realized_pnl_krw}")
    print(f"open_bot_cost_basis_krw={summary.open_bot_cost_basis_krw}")
    print(f"accounting_status={summary.accounting_status}")


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        with SessionLocal() as session:
            scopes = _scopes(session, namespace.user_id)
            print(f"mode={'APPLY' if namespace.apply else 'DRY_RUN'}")
            print(f"scope_count={len(scopes)}")
            for user_id, exchange in scopes:
                calculation = BotTradingPnlService(session).rebuild(
                    user_id, exchange=exchange, apply=namespace.apply
                )
                _print_result(calculation)
            if namespace.apply:
                session.commit()
            else:
                session.rollback()
    except Exception as error:
        print(f"Bot trading PnL rebuild failed. error_type={type(error).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
