"""Telegram transport adapter for policy callbacks in the existing listener."""

from dataclasses import fields
import json

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.services.telegram_policy_review_service import (
    TelegramPolicyReviewResult,
    TelegramPolicyReviewService,
)


def process_policy_callback(telegram, callback: dict, *, session_factory) -> None:
    try:
        result = TelegramPolicyReviewService(
            session_factory, settings=get_settings()
        ).process(callback)
    except Exception as error:
        result = TelegramPolicyReviewResult(
            request_status="EXECUTION_ERROR",
            safe_reason=type(error).__name__,
            answer="정책 요청을 처리할 수 없습니다. 잠시 후 상태를 다시 확인해 주세요.",
        )
    callback_id = callback.get("id")
    if isinstance(callback_id, str) and callback_id:
        result.external_calls = True
        try:
            telegram.answer_callback_query(
                callback_query_id=callback_id,
                text=result.answer,
                show_alert=result.request_status
                in {"DENIED", "DISABLED", "EXECUTION_ERROR"},
            )
        except Exception as error:
            print(f"Policy callback answer failed. error_type={type(error).__name__}")
    if result.message is not None:
        message = callback["message"]
        result.external_calls = True
        try:
            telegram.edit_message_text(
                chat_id=message["chat"]["id"],
                message_id=message["message_id"],
                text=result.message,
                reply_markup=result.markup,
            )
        except Exception as error:
            print(
                f"Policy callback message edit failed. error_type={type(error).__name__}"
            )
    report = {
        item.name: getattr(result, item.name)
        for item in fields(result)
        if item.name not in {"answer", "message", "markup"}
    }
    report["report_type"] = "TELEGRAM_POLICY_REVIEW_CALLBACK"
    print(json.dumps(report, sort_keys=True), flush=True)
