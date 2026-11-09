"""Plain-text policy review cards, with a separate confirmation step."""

from datetime import UTC

from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    restore_component_weights,
)
from crypto_trading_bot.services.shadow_review_gate_service import (
    ShadowReviewGateResult,
)


def _date(value):
    return (
        value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        if value
        else "자료 없음"
    )


def _number(value, unit=""):
    return f"{value:.4f}{unit}" if value is not None else "자료 없음"


def build_policy_review_detail(
    review: ShadowReviewGateResult, *, can_decide: bool
) -> str:
    enrollment, evidence = review.enrollment, review.performance
    name = "".join(
        char
        for char in " ".join(enrollment.scenario_name.split())
        if char.isprintable()
    )[:80]
    baseline = HeuristicMarketRankingPolicy().weights
    weights = restore_component_weights(enrollment.component_weights)
    lines = [
        "🔬 Crypto Trading Bot — 정책 상세 검토",
        f"Candidate ID: {review.candidate_id} / Enrollment ID: {enrollment.id}",
        f"전략: {name}",
        f"{enrollment.exchange} / {enrollment.quote_asset} · Top N {enrollment.effective_top_n}",
        "",
        "BASELINE → 후보 가중치 (차이)",
    ]
    for key, weight in sorted(weights.items()):
        original = getattr(baseline, key)
        lines.append(f"{key}: {original:.4f} → {weight:.4f} ({weight - original:+.4f})")
    lines.extend(
        [
            "",
            "Shadow 연구 근거",
            f"관측: {_date(evidence.first_success_captured_at)} ~ {_date(evidence.last_success_captured_at)}",
            f"선택 검증 표본: {evidence.successful_selection_count}",
        ]
    )
    costs = {item.horizon_minutes: item for item in evidence.cost_adjusted}
    for gross in evidence.gross:
        cost = costs[gross.horizon_minutes]
        lines.append(
            f"{gross.horizon_minutes}분 · Gross 차이 {_number(gross.mean_return_delta, '%p')}"
            f" / 비용 반영 차이 {_number(cost.mean_cost_adjusted_return_delta, '%p')}"
            f" · 표본 {gross.successful_comparable_snapshot_count}/{cost.cost_adjustable_shadow_snapshot_count}"
        )
    lines.extend(
        [
            "",
            f"Gate: {review.status}",
            f"PASS {len(review.passed_checks)} / FAIL {len(review.failed_checks)} / INSUFFICIENT {len(review.insufficient_checks)}",
            f"Review Policy Signature: {review.review_policy_signature}",
            f"검토 근거 기준: {_date(evidence.performance_evidence_as_of)}",
            "현재 LIVE Ranking: BASELINE (검토 시점)",
            "연구 결과이며 실제 계좌 수익을 보장하지 않습니다.",
            "정책을 승인해도 LIVE Ranking은 변경되지 않습니다. 적용은 별도 수동 절차입니다.",
        ]
    )
    if not can_decide:
        lines.append("현재 Telegram 정책 확정 기능은 비활성 상태입니다.")
    message = "\n".join(lines)
    if len(message.encode("utf-16-le")) // 2 > 4096:
        raise ValueError("Policy review detail exceeds Telegram message length")
    return message


def confirmation_keyboard(token: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ 정책 승인 확정",
                    "callback_data": f"policy_confirm:{token}",
                },
                {"text": "❌ 정책 거절", "callback_data": f"policy_reject:{token}"},
            ]
        ]
    }
