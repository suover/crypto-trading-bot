from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_trading_bot.db.models import ApprovalRequest, OrderLog, TradeRecommendation
from crypto_trading_bot.services.approval_decision_service import ApprovalDecisionResult
from crypto_trading_bot.services.approved_order_execution_service import (
    ApprovedOrderExecutionResult,
)
from crypto_trading_bot.services.live_order_execution_service import (
    LIVE_ORDER_EXECUTED_CANCELLED_STATUS,
    LIVE_ORDER_FAILED_STATUS,
    LIVE_ORDER_STATUS,
    LIVE_ORDER_UNKNOWN_STATUS,
    LIVE_ORDER_WAIT_STATUS,
    LiveOrderExecutionResult,
)
from crypto_trading_bot.services.mock_order_attempt_service import (
    MockOrderAttemptResult,
)
from scripts.run_telegram_approval_listener import (
    build_decision_result_message,
    build_superseded_message,
    get_user_error_message,
)


def build_approval_decision_result(
    *,
    decision: str = "APPROVE",
    already_processed: bool = False,
) -> ApprovalDecisionResult:
    recommendation = TradeRecommendation(
        id=1,
        analysis_run_id=1,
        market_snapshot_id=None,
        user_id=1,
        exchange="UPBIT",
        market="KRW-BTC",
        action="BUY",
        confidence=Decimal("0.7500"),
        reason="test recommendation",
        recommended_amount_krw=Decimal("5000"),
        recommended_quantity=None,
        ai_model="TEST",
        ai_response={},
        status="APPROVED" if decision == "APPROVE" else "REJECTED",
    )
    approval_request = ApprovalRequest(
        id=20,
        recommendation_id=1,
        user_id=1,
        status="APPROVED" if decision == "APPROVE" else "REJECTED",
        telegram_chat_id=123,
        telegram_message_id=456,
        callback_token="token",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    return ApprovalDecisionResult(
        approval_request=approval_request,
        recommendation=recommendation,
        decision=decision,  # type: ignore[arg-type]
        already_processed=already_processed,
    )


def build_order_log(
    *,
    trading_mode: str,
    status: str,
    exchange_order_id: str | None = None,
) -> OrderLog:
    return OrderLog(
        id=10,
        recommendation_id=1,
        approval_request_id=20,
        user_id=1,
        trading_mode=trading_mode,
        exchange="UPBIT",
        market="KRW-BTC",
        side="BUY",
        order_type="MARKET",
        amount_krw=Decimal("5000"),
        quantity=Decimal("0.0001"),
        price=Decimal("50000000"),
        status=status,
        exchange_order_id=exchange_order_id,
        error_message=None,
        raw_response={},
    )


def test_build_decision_result_message_for_mock_execution() -> None:
    result = build_approval_decision_result()
    order_log = build_order_log(
        trading_mode="MOCK",
        status="MOCK_FILLED",
    )
    mock_result = MockOrderAttemptResult(
        recommendation_id=1,
        approval_request_id=20,
        attempt_id=100,
        attempt_number=1,
        status="EXECUTED",
        order_log_id=order_log.id,
    )
    order_execution_result = ApprovedOrderExecutionResult(
        execution_mode="MOCK",
        order_log=order_log,
        mock_order_attempt_result=mock_result,
    )

    message = build_decision_result_message(
        original_text="테스트 승인 요청\n\n아래 버튼을 눌러 승인 또는 거절해 주세요.",
        result=result,
        order_execution_result=order_execution_result,
    )

    assert "처리 결과: 승인 완료" in message
    assert "주문 실행 모드: MOCK" in message
    assert "주문 상태: 모의 주문 완료" in message
    assert "※ 실제 업비트 주문은 실행되지 않았습니다." in message


def test_build_decision_result_message_for_live_execution() -> None:
    result = build_approval_decision_result()
    order_log = build_order_log(
        trading_mode="LIVE",
        status=LIVE_ORDER_STATUS,
        exchange_order_id="live-order-id",
    )
    live_result = LiveOrderExecutionResult(
        order_log=order_log,
        recommendation=result.recommendation,
        already_executed=False,
    )
    order_execution_result = ApprovedOrderExecutionResult(
        execution_mode="LIVE",
        order_log=order_log,
        live_order_execution_result=live_result,
    )

    message = build_decision_result_message(
        original_text="테스트 승인 요청\n\n아래 버튼을 눌러 승인 또는 거절해 주세요.",
        result=result,
        order_execution_result=order_execution_result,
    )

    assert "처리 결과: 승인 완료" in message
    assert "주문 실행 모드: LIVE" in message
    assert "주문 상태: 실거래 주문 확인" in message
    assert "로컬 상태: LIVE_PLACED" in message
    assert "거래소 주문 ID: live-order-id" in message


def test_build_decision_result_message_for_executed_cancelled_order() -> None:
    result = build_approval_decision_result()
    order_log = build_order_log(
        trading_mode="LIVE",
        status=LIVE_ORDER_EXECUTED_CANCELLED_STATUS,
        exchange_order_id="partially-filled-order-id",
    )
    live_result = LiveOrderExecutionResult(
        order_log=order_log,
        recommendation=result.recommendation,
        already_executed=False,
        outcome="CONFIRMED",
        pending=False,
    )

    message = build_decision_result_message(
        original_text="승인 요청",
        result=result,
        order_execution_result=ApprovedOrderExecutionResult(
            execution_mode="LIVE",
            order_log=order_log,
            live_order_execution_result=live_result,
        ),
    )

    assert "주문 상태: 실거래 체결 확인 (미체결 잔량 취소)" in message
    assert "로컬 상태: LIVE_EXECUTED_CANCELLED" in message


def test_build_decision_result_message_for_live_unknown() -> None:
    result = build_approval_decision_result()
    order_log = build_order_log(trading_mode="LIVE", status=LIVE_ORDER_UNKNOWN_STATUS)
    order_log.raw_response = {"identifier": "recommendation-1"}
    live_result = LiveOrderExecutionResult(
        order_log=order_log,
        recommendation=result.recommendation,
        already_executed=False,
        outcome="UNKNOWN",
    )
    message = build_decision_result_message(
        original_text="승인 요청",
        result=result,
        order_execution_result=ApprovedOrderExecutionResult(
            execution_mode="LIVE",
            order_log=order_log,
            live_order_execution_result=live_result,
        ),
    )
    assert "주문 상태: 실거래 결과 확인 필요" in message
    assert "실제 주문 실행 여부: 확인 불가" in message
    assert "동일 추천을 다시 승인하거나 수동으로 재주문하지 마세요" in message


def test_build_decision_result_message_for_recovered_pending_live_order() -> None:
    result = build_approval_decision_result()
    order_log = build_order_log(
        trading_mode="LIVE",
        status=LIVE_ORDER_WAIT_STATUS,
        exchange_order_id="recovered-uuid",
    )
    live_result = LiveOrderExecutionResult(
        order_log=order_log,
        recommendation=result.recommendation,
        already_executed=False,
        recovered=True,
        pending=True,
    )
    message = build_decision_result_message(
        original_text="승인 요청",
        result=result,
        order_execution_result=ApprovedOrderExecutionResult(
            execution_mode="LIVE",
            order_log=order_log,
            live_order_execution_result=live_result,
        ),
    )
    assert "실거래 주문 접수(완료 확인 대기)" in message
    assert "identifier 조회를 통해 기존 주문을 복구했습니다" in message


def test_build_decision_result_message_for_confirmed_live_failure() -> None:
    result = build_approval_decision_result()
    order_log = build_order_log(trading_mode="LIVE", status=LIVE_ORDER_FAILED_STATUS)
    order_log.error_message = "safe rejection"
    live_result = LiveOrderExecutionResult(
        order_log=order_log,
        recommendation=result.recommendation,
        already_executed=True,
        outcome="FAILED",
    )
    message = build_decision_result_message(
        original_text="승인 요청",
        result=result,
        order_execution_result=ApprovedOrderExecutionResult(
            execution_mode="LIVE",
            order_log=order_log,
            live_order_execution_result=live_result,
        ),
    )
    assert "실거래 주문 실패" in message
    assert "실제 주문 실행 여부: 실행되지 않음" in message
    assert "이미 처리된 실거래 주문 결과" in message


def test_build_decision_result_message_for_order_execution_error() -> None:
    result = build_approval_decision_result()
    order_execution_result = ApprovedOrderExecutionResult(
        execution_mode="UNKNOWN",
        error_message="Order execution mode is not ready",
    )

    message = build_decision_result_message(
        original_text="테스트 승인 요청\n\n아래 버튼을 눌러 승인 또는 거절해 주세요.",
        result=result,
        order_execution_result=order_execution_result,
    )

    assert "주문 상태: 주문 실행 모드 확인 실패" in message
    assert "오류 사유: Order execution mode is not ready" in message


def test_build_decision_result_message_for_reject() -> None:
    result = build_approval_decision_result(
        decision="REJECT",
    )

    message = build_decision_result_message(
        original_text="테스트 승인 요청\n\n아래 버튼을 눌러 승인 또는 거절해 주세요.",
        result=result,
        order_execution_result=None,
    )

    assert "처리 결과: 거절 완료" in message
    assert "주문 상태: 미실행" in message


def test_build_superseded_message_removes_action_prompt() -> None:
    message = build_superseded_message(
        "테스트 승인 요청\n\n아래 버튼을 눌러 승인 또는 거절해 주세요."
    )

    assert "테스트 승인 요청" in message
    assert "아래 버튼을 눌러 승인 또는 거절해 주세요." not in message
    assert "처리 결과: 최신 AI 분석으로 대체됨" in message
    assert "주문 상태: 미실행" in message
    assert "최신 Telegram 승인 요청 또는 최신 요약 메시지" in message


def test_get_user_error_message_for_superseded_request() -> None:
    message = get_user_error_message(
        ValueError("Approval request is not pending. status=SUPERSEDED")
    )

    assert message == "최신 AI 분석 결과로 대체된 승인 요청입니다."
