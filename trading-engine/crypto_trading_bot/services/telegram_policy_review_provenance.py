"""Canonical evidence binding and receipt identity for policy approval."""

from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    ShadowPolicyEvaluation,
    ShadowReviewNotification,
    StrategyReplayCandidateOutcome,
    TelegramPolicyReviewRequest,
)
from crypto_trading_bot.services.shadow_review_gate_service import (
    ShadowReviewGateResult,
    shadow_review_decision_payload,
    shadow_review_evidence_provenance,
)


def _canonical(value):
    if isinstance(value, Decimal):
        if value == 0:
            return "0"
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("Evidence timestamp must be timezone-aware")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if hasattr(value, "__dict__"):
        return _canonical(vars(value))
    return value


def review_evidence_fingerprint(
    session: Session, review: ShadowReviewGateResult
) -> str:
    """Compare the full evaluated evidence while allowing the evaluation clock to advance."""
    payload = shadow_review_decision_payload(review)
    payload.pop("review_evaluated_at")
    payload.pop("performance_evidence_as_of")
    performance = review.performance
    enrollment = review.enrollment
    as_of = performance.performance_evidence_as_of
    evaluation = ShadowPolicyEvaluation
    evaluation_signatures = session.execute(
        select(evaluation.strategy_replay_snapshot_id, evaluation.evaluation_signature)
        .where(
            evaluation.shadow_enrollment_id == enrollment.id,
            evaluation.strategy_replay_snapshot_id.in_(
                performance.timeline_snapshot_ids
            ),
            evaluation.evaluated_at <= as_of,
            evaluation.created_at <= as_of,
        )
        .order_by(evaluation.strategy_replay_snapshot_id)
    ).all()
    # Keep individual selected-market outcomes as well as performance aggregates.
    # Equal and opposite outcome changes can otherwise leave averages unchanged.
    selected = {
        (row.snapshot_id, item.horizon_minutes): (
            set(getattr(row, "baseline_top_markets", ()))
            | set(getattr(row, "scenario_top_markets", ()))
        )
        for item in performance.gross
        for row in item.snapshots
    }
    outcome = StrategyReplayCandidateOutcome
    outcome_values = []
    if selected:
        columns = tuple(outcome.__table__.columns)
        rows = session.execute(
            select(*columns)
            .where(
                outcome.strategy_replay_snapshot_id.in_({key[0] for key in selected}),
                outcome.horizon_minutes.in_(performance.requested_horizons),
                outcome.user_id == enrollment.user_id,
                outcome.exchange == enrollment.exchange,
                outcome.target_at <= as_of,
                outcome.evaluated_at <= as_of,
                outcome.created_at <= as_of,
                outcome.updated_at <= as_of,
            )
            .order_by(outcome.id)
        ).mappings()
        outcome_values = [
            dict(row)
            for row in rows
            if row["market"]
            in selected.get(
                (row["strategy_replay_snapshot_id"], row["horizon_minutes"]), ()
            )
        ]
    semantic = {
        "decision": payload,
        "provenance": shadow_review_evidence_provenance(review),
        "performance": {
            key: value
            for key, value in vars(performance).items()
            if key not in {"enrollment", "performance_evidence_as_of"}
        },
        "enrollment_watermark": (
            enrollment.shadow_snapshot_id_watermark,
            enrollment.shadow_captured_at_watermark,
        ),
        "weights": enrollment.component_weights,
        "evaluation_signatures": [tuple(row) for row in evaluation_signatures],
        "outcomes": outcome_values,
    }
    data = json.dumps(
        _canonical(semantic), sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return f"telegram-shadow-evidence-v1:{sha256(data.encode('utf-8')).hexdigest()}"


def notification_matches(
    request: TelegramPolicyReviewRequest, notification: ShadowReviewNotification | None
) -> bool:
    return (
        notification is not None
        and notification.delivery_status == "SENT"
        and notification.id == request.notification_id
        and notification.candidate_id == request.candidate_id
        and notification.shadow_enrollment_id == request.shadow_enrollment_id
        and notification.review_policy_signature == request.review_policy_signature
        and notification.notification_destination == str(request.telegram_chat_id)
        and request.telegram_message_id is not None
        and notification.telegram_message_id == request.telegram_message_id
    )


def telegram_approval_context(request: TelegramPolicyReviewRequest) -> dict:
    return _canonical(
        {
            "request_id": request.id,
            "notification_id": request.notification_id,
            "telegram_user_id": request.authorized_telegram_user_id,
            "telegram_chat_id": request.telegram_chat_id,
            "telegram_message_id": request.telegram_message_id,
            "reviewed_at": request.reviewed_at,
            "evidence_fingerprint": request.evidence_fingerprint,
        }
    )
