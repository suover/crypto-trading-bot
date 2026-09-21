import argparse
from collections import defaultdict
from decimal import Decimal
from statistics import median

from sqlalchemy import select

from crypto_trading_bot.db.models import (
    TradeRecommendation,
    TradeRecommendationCandidateOutcome,
    TradeRecommendationOutcome,
)


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report recommendation outcomes (DB-only)."
    )
    parser.add_argument("--user-id", type=int)
    return parser.parse_args(args)


def _mean(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal("0")) / len(values) if values else None


def _correlation(pairs: list[tuple[Decimal, Decimal]]) -> Decimal | None:
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs, strict=True)
    mean_x = _mean(list(xs))
    mean_y = _mean(list(ys))
    numerator = sum(((x - mean_x) * (y - mean_y) for x, y in pairs), Decimal("0"))
    x_squared = sum(((x - mean_x) ** 2 for x in xs), Decimal("0"))
    y_squared = sum(((y - mean_y) ** 2 for y in ys), Decimal("0"))
    denominator = (x_squared * y_squared).sqrt()
    return numerator / denominator if denominator else None


def source_classification(ai_response: object) -> str:
    if not isinstance(ai_response, dict):
        return "UNKNOWN"
    source = str(ai_response.get("source", "")).strip().lower()
    override = ai_response.get("safety_override")
    if source == "system_guard":
        return "SYSTEM_GUARD"
    if source == "openai" and override:
        return "OPENAI_SAFETY_OVERRIDE"
    if source == "openai":
        return "OPENAI_NO_SAFETY_OVERRIDE"
    return "UNKNOWN"


def build_report(session, user_id: int | None = None) -> list[str]:
    query = select(TradeRecommendationOutcome, TradeRecommendation).join(
        TradeRecommendation,
        TradeRecommendation.id == TradeRecommendationOutcome.recommendation_id,
    )
    if user_id is not None:
        query = query.where(TradeRecommendationOutcome.user_id == user_id)
    rows = tuple(session.execute(query))
    lines = ["report_type=RECOMMENDATION_SIGNAL_OUTCOME (not execution PnL)"]
    source_counts: dict[str, int] = defaultdict(int)
    for _, recommendation in rows:
        source_counts[source_classification(recommendation.ai_response)] += 1
    for source, count in sorted(source_counts.items()):
        lines.append(f"recommendation_source={source} outcome_count={count}")
    grouped: dict[tuple[int, str], list[tuple]] = defaultdict(list)
    for outcome, recommendation in rows:
        grouped[(outcome.horizon_minutes, recommendation.action)].append(
            (outcome, recommendation)
        )
    for (horizon, action), items in sorted(grouped.items()):
        complete = [item for item in items if item[0].evaluation_status == "COMPLETE"]
        market = [Decimal(item[0].market_return_percentage) for item in complete]
        aligned = [
            Decimal(item[0].action_aligned_return_percentage)
            for item in complete
            if item[0].action_aligned_return_percentage is not None
        ]
        directions = defaultdict(int)
        for outcome, _ in complete:
            directions[outcome.directional_result] += 1
        directional_total = directions["WIN"] + directions["LOSS"] + directions["FLAT"]
        hit_rate = (
            Decimal(directions["WIN"]) / Decimal(directional_total) * Decimal("100")
            if directional_total
            else None
        )
        lines.append(
            f"horizon={horizon} action={action} count={len(items)} "
            f"complete={len(complete)} partial={len(items) - len(complete)} "
            f"market_mean={_mean(market)} market_median={median(market) if market else None} "
            f"aligned_mean={_mean(aligned)} aligned_median={median(aligned) if aligned else None} "
            f"win={directions['WIN']} loss={directions['LOSS']} "
            f"flat={directions['FLAT']} gross_directional_hit_rate={hit_rate}"
        )
        if action in {"BUY", "SELL"}:
            confidence_pairs = [
                (
                    Decimal(item[1].confidence),
                    Decimal(item[0].action_aligned_return_percentage),
                )
                for item in complete
                if item[1].confidence is not None
                and item[0].action_aligned_return_percentage is not None
            ]
            lines.append(
                f"horizon={horizon} action={action} confidence_diagnostic_count="
                f"{len(confidence_pairs)} mean_confidence="
                f"{_mean([pair[0] for pair in confidence_pairs])} "
                f"mean_aligned_return={_mean([pair[1] for pair in confidence_pairs])} "
                f"correlation={_correlation(confidence_pairs)}"
            )

    candidate_rows = tuple(session.scalars(select(TradeRecommendationCandidateOutcome)))
    by_key: dict[tuple[int, int], list] = defaultdict(list)
    for row in candidate_rows:
        if row.evaluation_status == "COMPLETE":
            by_key[(row.recommendation_id, row.horizon_minutes)].append(row)
    recommendations = {row.id: row for _, row in rows}
    for (recommendation_id, horizon), candidates in sorted(by_key.items()):
        recommendation = recommendations.get(recommendation_id)
        if recommendation is None or recommendation.action not in {"BUY", "HOLD"}:
            continue
        eligible = [row for row in candidates if row.buy_eligible]
        if not eligible:
            continue
        ranked = sorted(
            eligible,
            key=lambda row: Decimal(row.market_return_percentage),
            reverse=True,
        )
        selected = next((row for row in candidates if row.is_selected), None)
        if selected is None:
            continue
        selected_return = Decimal(selected.market_return_percentage)
        best_return = Decimal(ranked[0].market_return_percentage)
        label = (
            "descriptive_only"
            if recommendation.action == "HOLD"
            else "selection_quality"
        )
        lines.append(
            f"recommendation_id={recommendation_id} horizon={horizon} {label} "
            f"selected_market={selected.market} selected_return={selected_return} "
            f"best_market={ranked[0].market} best_return={best_return} "
            f"selected_realized_rank="
            f"{ranked.index(selected) + 1 if selected in ranked else None} "
            f"gap_to_best={selected_return - best_return} "
            f"source={source_classification(recommendation.ai_response)}"
        )
    return lines


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        with SessionLocal() as session:
            for line in build_report(session, namespace.user_id):
                print(line)
    except Exception as error:
        print(
            f"Recommendation outcome report failed. error_type={type(error).__name__}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
