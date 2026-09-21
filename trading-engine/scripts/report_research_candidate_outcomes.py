import argparse
from collections import defaultdict
from decimal import Decimal
from statistics import median

from sqlalchemy import select

from crypto_trading_bot.db.models import (
    StrategyReplayCandidate,
    StrategyReplayCandidateOutcome,
)


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report research candidate outcomes (DB-only)."
    )
    parser.add_argument("--user-id", type=int)
    parser.add_argument("--limit", type=int, default=50)
    namespace = parser.parse_args(args)
    if namespace.user_id is not None and namespace.user_id <= 0:
        parser.error("--user-id must be positive")
    if namespace.limit <= 0:
        parser.error("--limit must be positive")
    return namespace


def _mean(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else None


def build_report(session, *, user_id: int | None = None, limit: int = 50) -> list[str]:
    query = select(StrategyReplayCandidateOutcome)
    if user_id is not None:
        query = query.where(StrategyReplayCandidateOutcome.user_id == user_id)
    outcomes = tuple(session.scalars(query.execution_options(autoflush=False)))
    complete = [row for row in outcomes if row.evaluation_status == "COMPLETE"]
    lines = [
        "report_type=RESEARCH_CANDIDATE_OUTCOME",
        "result_type=GROSS_MARKET_MOVEMENT_NOT_TRADING_PNL",
        f"outcome_count={len(outcomes)}",
        f"complete_count={len(complete)}",
        f"partial_count={len(outcomes) - len(complete)}",
    ]
    grouped: dict[int, list[StrategyReplayCandidateOutcome]] = defaultdict(list)
    for outcome in outcomes:
        grouped[outcome.horizon_minutes].append(outcome)
    for horizon, rows in sorted(grouped.items()):
        completed = [row for row in rows if row.evaluation_status == "COMPLETE"]
        returns = [Decimal(row.market_return_percentage) for row in completed]
        lines.append(
            f"horizon_minutes={horizon} total={len(rows)} "
            f"complete={len(completed)} partial={len(rows) - len(completed)} "
            f"mean_market_return={_mean(returns)} "
            f"median_market_return={median(returns) if returns else None}"
        )
    detail_query = (
        select(StrategyReplayCandidateOutcome, StrategyReplayCandidate)
        .join(
            StrategyReplayCandidate,
            StrategyReplayCandidate.id
            == StrategyReplayCandidateOutcome.strategy_replay_candidate_id,
        )
        .order_by(
            StrategyReplayCandidateOutcome.snapshot_at.desc(),
            StrategyReplayCandidateOutcome.id.desc(),
        )
        .limit(limit)
        .execution_options(autoflush=False)
    )
    if user_id is not None:
        detail_query = detail_query.where(
            StrategyReplayCandidateOutcome.user_id == user_id
        )
    for outcome, candidate in session.execute(detail_query):
        lines.append(
            f"snapshot_id={outcome.strategy_replay_snapshot_id} "
            f"market={outcome.market} original_rank={candidate.original_rank} "
            f"horizon_minutes={outcome.horizon_minutes} "
            f"reference_price={outcome.reference_price} end_price={outcome.end_price} "
            f"market_return_percentage={outcome.market_return_percentage} "
            f"status={outcome.evaluation_status} safe_reason={outcome.safe_reason}"
        )
    return lines


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        with SessionLocal() as session:
            for line in build_report(
                session, user_id=namespace.user_id, limit=namespace.limit
            ):
                print(line)
    except Exception as error:
        print(
            "Research candidate outcome report failed. "
            f"error_type={type(error).__name__}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
