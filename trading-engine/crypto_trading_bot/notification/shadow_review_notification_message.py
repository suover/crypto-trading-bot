"""Plain-text notification for an already validated Shadow Review decision."""

from datetime import UTC

from crypto_trading_bot.services.shadow_review_gate_service import (
    ELIGIBLE_FOR_PROMOTION_REVIEW,
    ShadowReviewGateResult,
)


def build_shadow_review_message(review: ShadowReviewGateResult) -> str:
    if (
        review.status != ELIGIBLE_FOR_PROMOTION_REVIEW
        or not review.eligible_for_promotion_review
    ):
        raise ValueError("Shadow Review is not eligible")
    enrollment = review.enrollment
    scenario = " ".join(enrollment.scenario_name.split())
    # Strip control/format characters to keep data on one visible line.
    scenario = "".join(character for character in scenario if character.isprintable())[
        :80
    ]
    as_of = review.performance.performance_evidence_as_of.astimezone(UTC)
    message = (
        "🔬 Crypto Trading Bot — Shadow Review 알림\n\n"
        "Shadow Review Gate를 통과한 정책을 검토해 주세요.\n\n"
        f"Candidate ID: {review.candidate_id}\n"
        f"Shadow Enrollment ID: {enrollment.id}\n"
        f"전략: {scenario}\n"
        f"거래소: {enrollment.exchange} / {enrollment.quote_asset}\n"
        f"Top N: {enrollment.effective_top_n}\n"
        f"상태: {review.status}\n"
        f"통과한 검증 항목: {len(review.passed_checks)}개\n"
        f"Shadow Evidence 기준 시각: {as_of:%Y-%m-%d %H:%M:%S} UTC\n"
        "무결성 검증: 정상\n\n"
        "정책 상태: 미승인. 연구 결과는 투자 수익을 보장하지 않습니다.\n"
        "정책 승인과 LIVE Ranking 활성화에는 별도 수동 절차가 필요합니다.\n"
        "이 알림은 LIVE Ranking을 변경하지 않습니다."
    )
    if len(message.encode("utf-16-le")) // 2 > 4096:
        raise ValueError("Shadow Review message exceeds Telegram's message limit")
    return message
