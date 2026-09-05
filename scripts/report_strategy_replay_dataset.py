import argparse

from sqlalchemy import func, select

from crypto_trading_bot.db.models import (
    StrategyReplayCandidate,
    StrategyReplaySnapshot,
)


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report strategy replay dataset (DB-only)."
    )
    parser.add_argument("--limit", type=int, default=50)
    namespace = parser.parse_args(args)
    if namespace.limit <= 0:
        parser.error("--limit must be positive")
    return namespace


def build_report(session, *, limit: int = 50) -> list[str]:
    snapshot_count = session.scalar(
        select(func.count()).select_from(StrategyReplaySnapshot)
    )
    candidate_count = session.scalar(
        select(func.count()).select_from(StrategyReplayCandidate)
    )
    lines = [
        "report_type=STRATEGY_REPLAY_DATASET",
        f"snapshot_count={snapshot_count}",
        f"research_candidate_count={candidate_count}",
    ]
    latest = session.scalar(
        select(StrategyReplaySnapshot)
        .order_by(
            StrategyReplaySnapshot.captured_at.desc(),
            StrategyReplaySnapshot.id.desc(),
        )
        .limit(1)
    )
    if latest is None:
        lines.append("latest_snapshot_id=None")
        return lines
    in_prefilter_count = session.scalar(
        select(func.count())
        .select_from(StrategyReplayCandidate)
        .where(
            StrategyReplayCandidate.strategy_replay_snapshot_id == latest.id,
            StrategyReplayCandidate.in_prefilter.is_(True),
        )
    )
    final_selected_count = session.scalar(
        select(func.count())
        .select_from(StrategyReplayCandidate)
        .where(
            StrategyReplayCandidate.strategy_replay_snapshot_id == latest.id,
            StrategyReplayCandidate.final_selected.is_(True),
        )
    )
    held_count = session.scalar(
        select(func.count())
        .select_from(StrategyReplayCandidate)
        .where(
            StrategyReplayCandidate.strategy_replay_snapshot_id == latest.id,
            StrategyReplayCandidate.held.is_(True),
        )
    )
    candidates = tuple(
        session.scalars(
            select(StrategyReplayCandidate)
            .where(StrategyReplayCandidate.strategy_replay_snapshot_id == latest.id)
            .order_by(
                StrategyReplayCandidate.prefilter_rank.asc().nulls_last(),
                StrategyReplayCandidate.market,
            )
            .limit(limit)
        )
    )
    lines.extend(
        (
            f"latest_snapshot_id={latest.id}",
            f"latest_pipeline_run_id={latest.pipeline_run_id}",
            f"latest_policy_signature={latest.policy_signature}",
            f"latest_research_candidate_count={latest.research_candidate_count}",
            f"latest_prefilter_candidate_count={latest.prefilter_candidate_count}",
            f"latest_ranked_candidate_count={latest.ranked_candidate_count}",
            f"latest_final_candidate_count={latest.final_candidate_count}",
            f"in_prefilter_count={in_prefilter_count}",
            f"final_selected_count={final_selected_count}",
            f"held_count={held_count}",
        )
    )
    for row in candidates:
        lines.append(
            f"market={row.market} prefilter_rank={row.prefilter_rank} "
            f"original_rank={row.original_rank} original_score={row.original_score} "
            f"final_selected={row.final_selected} selection_source={row.selection_source}"
        )
    return lines


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        with SessionLocal() as session:
            for line in build_report(session, limit=namespace.limit):
                print(line)
    except Exception as error:
        print(f"Strategy replay report failed. error_type={type(error).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
