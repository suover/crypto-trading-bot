import time
from datetime import datetime
from decimal import Decimal
from typing import Any

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import OrderLog
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.notification.telegram_client import TelegramClient
from crypto_trading_bot.services.approval_decision_service import (
    ApprovalDecisionResult,
    ApprovalDecisionService,
)
from crypto_trading_bot.services.approval_request_service import (
    ApprovalRequestService,
)
from crypto_trading_bot.services.mock_order_attempt_service import (
    MockOrderAttemptResult,
    MockOrderAttemptService,
)
from crypto_trading_bot.services.mock_order_execution_service import (
    MockOrderExecutionError,
)


CALLBACK_DECISIONS = {
    "approve": "APPROVE",
    "reject": "REJECT",
}

EMPTY_INLINE_KEYBOARD: dict[str, list[Any]] = {
    "inline_keyboard": [],
}

ACTION_PROMPT = "아래 버튼을 눌러 승인 또는 거절해 주세요."


def parse_callback_data(callback_data: str) -> tuple[str, str]:
    command, separator, callback_token = callback_data.partition(":")

    normalized_command = command.strip().lower()
    normalized_token = callback_token.strip()

    if separator != ":":
        raise ValueError(f"Invalid callback data format. callback_data={callback_data}")

    decision = CALLBACK_DECISIONS.get(normalized_command)

    if decision is None:
        raise ValueError(f"Unsupported callback command. command={normalized_command}")

    if not normalized_token:
        raise ValueError("Callback token must not be empty")

    return decision, normalized_token


def format_decimal(
    value: object | None,
    decimal_places: int = 2,
) -> str:
    if value is None:
        return "-"

    decimal_value = Decimal(str(value))

    return f"{decimal_value:,.{decimal_places}f}"


def format_optional_datetime(
    value: datetime | None,
) -> str:
    if value is None:
        return "-"

    return value.strftime("%Y-%m-%d %H:%M:%S")


def build_decision_result_message(
    original_text: str,
    result: ApprovalDecisionResult,
    mock_order_attempt_result: MockOrderAttemptResult | None = None,
    mock_order_log: OrderLog | None = None,
    mock_order_error: str | None = None,
) -> str:
    base_text = remove_action_prompt(original_text)

    message_lines = [
        base_text,
        "",
        "------------------------------",
    ]

    if result.decision == "REJECT":
        message_lines.extend(
            [
                "처리 결과: 거절 완료",
                "주문 상태: 미실행",
                "※ 거절되어 실제 업비트 주문은 실행되지 않습니다.",
            ]
        )

    elif mock_order_attempt_result is not None:
        attempt_status = mock_order_attempt_result.status

        if attempt_status in {
            "EXECUTED",
            "ALREADY_EXECUTED",
        }:
            if mock_order_log is None:
                message_lines.extend(
                    [
                        "처리 결과: 승인 완료",
                        "주문 상태: 모의 주문 결과 조회 실패",
                        "",
                        "※ 실제 업비트 주문은 실행되지 않았습니다.",
                    ]
                )
            else:
                message_lines.extend(
                    [
                        "처리 결과: 승인 완료",
                        "주문 상태: 모의 주문 완료",
                        f"마켓: {mock_order_log.market}",
                        f"매매 구분: {mock_order_log.side}",
                        f"주문 방식: {mock_order_log.order_type}",
                        (f"주문 금액: {format_decimal(mock_order_log.amount_krw)}원"),
                        (f"기준 가격: {format_decimal(mock_order_log.price)}원"),
                        (f"모의 수량: {format_decimal(mock_order_log.quantity, 10)}"),
                        (f"실행 시도 번호: {mock_order_attempt_result.attempt_number}"),
                        "",
                        "※ 실제 업비트 주문은 실행되지 않았습니다.",
                    ]
                )

                if attempt_status == "ALREADY_EXECUTED":
                    message_lines.extend(
                        [
                            "",
                            "※ 이미 처리된 모의 주문 결과입니다.",
                        ]
                    )

        elif attempt_status == "RETRYABLE_FAILED":
            message_lines.extend(
                [
                    "처리 결과: 승인 완료",
                    "주문 상태: 모의 주문 실패 / 재시도 예정",
                    (f"실행 시도 번호: {mock_order_attempt_result.attempt_number}"),
                    (f"실패 코드: {mock_order_attempt_result.error_code}"),
                    (f"실패 사유: {mock_order_attempt_result.error_message}"),
                    (
                        "다음 재시도 예정: "
                        f"{
                            format_optional_datetime(
                                mock_order_attempt_result.next_retry_at
                            )
                        }"
                    ),
                    "",
                    "※ 실제 업비트 주문은 실행되지 않았습니다.",
                ]
            )

        elif attempt_status == "PERMANENT_FAILED":
            message_lines.extend(
                [
                    "처리 결과: 승인 완료",
                    "주문 상태: 모의 주문 실패 / 재시도 불가",
                    (f"실행 시도 번호: {mock_order_attempt_result.attempt_number}"),
                    (f"실패 코드: {mock_order_attempt_result.error_code}"),
                    (f"실패 사유: {mock_order_attempt_result.error_message}"),
                    "",
                    "※ 해당 요청은 자동 재시도되지 않습니다.",
                    "※ 실제 업비트 주문은 실행되지 않았습니다.",
                ]
            )

        elif attempt_status == "RETRY_EXHAUSTED":
            message_lines.extend(
                [
                    "처리 결과: 승인 완료",
                    "주문 상태: 모의 주문 최종 실패",
                    (f"실행 시도 번호: {mock_order_attempt_result.attempt_number}"),
                    (f"실패 코드: {mock_order_attempt_result.error_code}"),
                    (f"실패 사유: {mock_order_attempt_result.error_message}"),
                    "",
                    "※ 최대 재시도 횟수를 모두 사용했습니다.",
                    "※ 실제 업비트 주문은 실행되지 않았습니다.",
                ]
            )

        else:
            message_lines.extend(
                [
                    "처리 결과: 승인 완료",
                    (f"주문 상태: 알 수 없는 상태 ({attempt_status})"),
                    "",
                    "※ 실제 업비트 주문은 실행되지 않았습니다.",
                ]
            )

    elif mock_order_error is not None:
        message_lines.extend(
            [
                "처리 결과: 승인 완료",
                "주문 상태: 모의 주문 처리 오류",
                f"오류 사유: {mock_order_error}",
                "",
                "※ 실제 업비트 주문은 실행되지 않았습니다.",
            ]
        )

    else:
        message_lines.extend(
            [
                "처리 결과: 승인 완료",
                "주문 상태: 미실행",
                "※ 모의 주문 결과를 확인할 수 없습니다.",
            ]
        )

    if result.already_processed:
        message_lines.extend(
            [
                "",
                "※ 이미 같은 결정으로 처리된 승인 요청입니다.",
            ]
        )

    return "\n".join(message_lines)


def build_expired_message(original_text: str) -> str:
    base_text = remove_action_prompt(original_text)

    return "\n".join(
        [
            base_text,
            "",
            "------------------------------",
            "처리 결과: 승인 요청 만료",
            "주문 상태: 미실행",
            "※ 유효시간이 지나 승인하거나 거절할 수 없습니다.",
        ]
    )


def remove_action_prompt(original_text: str) -> str:
    lines = original_text.rstrip().splitlines()

    if lines and lines[-1].strip() == ACTION_PROMPT:
        lines.pop()

    while lines and not lines[-1].strip():
        lines.pop()

    return "\n".join(lines)


def get_user_error_message(error: ValueError) -> str:
    error_message = str(error).lower()

    if "expired" in error_message:
        return "승인 요청의 유효시간이 만료되었습니다."

    if "already been processed with a different decision" in error_message:
        return "이미 다른 결정으로 처리된 요청입니다."

    if "not found" in error_message:
        return "승인 요청을 찾을 수 없습니다."

    if "does not match" in error_message:
        return "승인 요청 정보가 일치하지 않습니다."

    if "not pending" in error_message:
        return "이미 처리됐거나 더 이상 유효하지 않은 요청입니다."

    return "승인 요청을 처리할 수 없습니다."


def require_dict(
    value: object,
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} is missing or invalid")

    return value


def require_string(
    value: object,
    field_name: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is missing or invalid")

    return value


def require_int(
    value: object,
    field_name: str,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} is missing or invalid")

    return value


def get_safe_error_summary(error: Exception) -> str:
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)

    return f"error_type={type(error).__name__}, status_code={status_code}"


def answer_callback_safely(
    telegram_client: TelegramClient,
    callback_query_id: str | None,
    text: str,
    show_alert: bool = False,
) -> None:
    if callback_query_id is None:
        return

    try:
        telegram_client.answer_callback_query(
            callback_query_id=callback_query_id,
            text=text,
            show_alert=show_alert,
        )
    except Exception as error:
        print(
            f"Failed to answer Telegram callback query. {get_safe_error_summary(error)}"
        )


def process_callback_update(
    telegram_client: TelegramClient,
    update: dict[str, Any],
    order_upbit_client: UpbitClient | None = None,
) -> None:
    callback_query_value = update.get("callback_query")

    # /start, 일반 텍스트 메시지 등은 처리하지 않음
    if not isinstance(callback_query_value, dict):
        return

    callback_query = callback_query_value
    callback_query_id: str | None = None
    chat_id: int | None = None
    message_id: int | None = None
    original_text = ""

    try:
        callback_query_id = require_string(
            callback_query.get("id"),
            "callback_query.id",
        )

        callback_data = require_string(
            callback_query.get("data"),
            "callback_query.data",
        )

        message = require_dict(
            callback_query.get("message"),
            "callback_query.message",
        )

        chat = require_dict(
            message.get("chat"),
            "callback_query.message.chat",
        )

        chat_id = require_int(
            chat.get("id"),
            "callback_query.message.chat.id",
        )

        message_id = require_int(
            message.get("message_id"),
            "callback_query.message.message_id",
        )

        message_text = message.get("text")

        if isinstance(message_text, str):
            original_text = message_text

        decision, callback_token = parse_callback_data(callback_data)

        with SessionLocal() as session:
            decision_service = ApprovalDecisionService(session)

            result = decision_service.process_decision(
                callback_token=callback_token,
                decision=decision,
                telegram_chat_id=chat_id,
                telegram_message_id=message_id,
            )

        mock_order_attempt_result: MockOrderAttemptResult | None = None
        mock_order_log: OrderLog | None = None
        mock_order_error: str | None = None

        if result.decision == "APPROVE":
            try:
                with SessionLocal() as session:
                    attempt_service = MockOrderAttemptService(
                        session=session,
                        upbit_client=order_upbit_client,
                    )

                    mock_order_attempt_result = attempt_service.execute(
                        recommendation_id=result.recommendation.id,
                        approval_request_id=result.approval_request.id,
                    )

                    if mock_order_attempt_result.order_log_id is not None:
                        mock_order_log = session.get(
                            OrderLog,
                            mock_order_attempt_result.order_log_id,
                        )

                        if mock_order_log is None:
                            raise MockOrderExecutionError(
                                "Mock order log was not found after execution. "
                                f"order_log_id="
                                f"{mock_order_attempt_result.order_log_id}"
                            )

            except MockOrderExecutionError as error:
                mock_order_error = str(error)

                print(
                    "Mock order attempt processing failed. "
                    f"recommendation_id={result.recommendation.id}, "
                    f"approval_request_id={result.approval_request.id}, "
                    f"error={error}"
                )

        result_message = build_decision_result_message(
            original_text=original_text,
            result=result,
            mock_order_attempt_result=mock_order_attempt_result,
            mock_order_log=mock_order_log,
            mock_order_error=mock_order_error,
        )

        # 처리 결과를 메시지에 표시하고 승인·거절 버튼 제거
        telegram_client.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=result_message,
            reply_markup=EMPTY_INLINE_KEYBOARD,
        )

        attempt_status = (
            mock_order_attempt_result.status
            if mock_order_attempt_result is not None
            else None
        )

        if result.decision == "REJECT":
            callback_answer = "매매 추천을 거절했습니다."

        elif attempt_status in {
            "EXECUTED",
            "ALREADY_EXECUTED",
        }:
            callback_answer = "승인 후 모의 주문을 완료했습니다."

        elif attempt_status == "RETRYABLE_FAILED":
            callback_answer = "모의 주문 재시도가 예약되었습니다."

        elif attempt_status == "PERMANENT_FAILED":
            callback_answer = "모의 주문을 실행할 수 없습니다."

        elif attempt_status == "RETRY_EXHAUSTED":
            callback_answer = "모의 주문 재시도 한도를 초과했습니다."

        else:
            callback_answer = "승인됐지만 모의 주문은 실행되지 않았습니다."

        answer_callback_safely(
            telegram_client=telegram_client,
            callback_query_id=callback_query_id,
            text=callback_answer,
        )

        mock_order_attempt_status = (
            mock_order_attempt_result.status
            if mock_order_attempt_result is not None
            else None
        )

        mock_order_attempt_number = (
            mock_order_attempt_result.attempt_number
            if mock_order_attempt_result is not None
            else None
        )

        print(
            "Telegram approval callback processed. "
            f"approval_request_id={result.approval_request.id}, "
            f"recommendation_id={result.recommendation.id}, "
            f"decision={result.decision}, "
            f"already_processed={result.already_processed}, "
            f"mock_order_attempt_status="
            f"{mock_order_attempt_status}, "
            f"mock_order_attempt_number="
            f"{mock_order_attempt_number}, "
            f"mock_order_error={mock_order_error}"
        )

    except ValueError as error:
        user_message = get_user_error_message(error)

        # 만료 요청은 버튼을 제거하고 메시지에도 만료 상태 표시
        if (
            "expired" in str(error).lower()
            and chat_id is not None
            and message_id is not None
            and original_text
        ):
            telegram_client.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=build_expired_message(original_text),
                reply_markup=EMPTY_INLINE_KEYBOARD,
            )

        answer_callback_safely(
            telegram_client=telegram_client,
            callback_query_id=callback_query_id,
            text=user_message,
            show_alert=True,
        )

        print(f"Telegram approval callback rejected. error={error}")

    except Exception:
        answer_callback_safely(
            telegram_client=telegram_client,
            callback_query_id=callback_query_id,
            text="승인 요청 처리 중 오류가 발생했습니다.",
            show_alert=True,
        )

        # 예상하지 못한 오류는 업데이트를 확정하지 않도록 상위로 전달
        raise


def expire_pending_approval_requests() -> int:
    with SessionLocal() as session:
        approval_request_service = ApprovalRequestService(session)

        return approval_request_service.expire_pending_requests()


def run_telegram_approval_listener(
    order_upbit_client: UpbitClient | None = None,
) -> None:
    telegram_client = TelegramClient()
    next_offset: int | None = None

    print("Telegram approval listener started.")
    print("Press Ctrl+C to stop.")

    while True:
        try:
            expired_count = expire_pending_approval_requests()

            if expired_count > 0:
                print(
                    "Expired pending approval requests cleaned up. "
                    f"count={expired_count}"
                )

            updates = telegram_client.get_updates(
                offset=next_offset,
                timeout=30,
            )

            for update in updates:
                update_id = require_int(
                    update.get("update_id"),
                    "update.update_id",
                )

                process_callback_update(
                    telegram_client=telegram_client,
                    update=update,
                    order_upbit_client=order_upbit_client,
                )

                # 정상 처리 또는 영구적으로 처리할 수 없는 이벤트만 확정
                next_offset = update_id + 1

        except KeyboardInterrupt:
            print("")
            print("Telegram approval listener stopped.")
            return

        except Exception as error:
            print(
                "Telegram approval listener error. "
                "retry_after_seconds=5, "
                f"{get_safe_error_summary(error)}"
            )
            time.sleep(5)


if __name__ == "__main__":
    run_telegram_approval_listener()
