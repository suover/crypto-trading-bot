import argparse

from crypto_trading_bot.services.full_live_policy_activation_service import (
    ALREADY_ACTIVE,
    CREATED,
    DRY_RUN,
    REPORT_TYPE,
    FullLivePolicyActivationService,
)


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview or activate an approved Full LIVE ranking policy."
    )
    parser.add_argument(
        "--promotion-approval-id", type=_positive_integer, required=True
    )
    parser.add_argument("--user-id", type=_positive_integer, required=True)
    parser.add_argument("--exchange", required=True)
    parser.add_argument("--quote-asset", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-approval-signature")
    namespace = parser.parse_args(args)
    if namespace.apply and not namespace.expected_approval_signature:
        parser.error("--apply requires --expected-approval-signature")
    if not namespace.apply and namespace.expected_approval_signature:
        parser.error("--expected-approval-signature is only valid with --apply")
    return namespace


def _bool(value: bool) -> str:
    return str(value).lower()


def report(result) -> list[str]:
    activation = result.activation
    return [
        f"report_type={REPORT_TYPE}",
        f"promotion_approval_id={result.promotion_approval_id}",
        f"candidate_id={result.candidate_id}",
        f"user_id={result.user_id}",
        f"exchange={result.exchange}",
        f"quote_asset={result.quote_asset}",
        f"scenario_name={result.scenario_name}",
        f"baseline_policy_signature={result.baseline_policy_signature}",
        f"current_baseline_policy_signature={result.current_baseline_policy_signature}",
        f"effective_policy_signature={result.effective_policy_signature}",
        f"activation_id={getattr(activation, 'id', None)}",
        f"activation_status={result.activation_status}",
        f"activation_signature={getattr(activation, 'activation_signature', None)}",
        f"database_write={_bool(result.database_write)}",
        f"live_policy_change={_bool(result.live_policy_change)}",
        f"live_order_change={_bool(result.live_order_change)}",
        f"external_calls={_bool(result.external_calls)}",
        f"safe_reason={result.safe_reason}",
    ]


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    service = FullLivePolicyActivationService(session)
    kwargs = {
        "promotion_approval_id": namespace.promotion_approval_id,
        "user_id": namespace.user_id,
        "exchange": namespace.exchange,
        "quote_asset": namespace.quote_asset,
    }
    result = (
        service.activate(
            **kwargs,
            expected_approval_signature=namespace.expected_approval_signature,
        )
        if namespace.apply
        else service.preview(**kwargs)
    )
    if result.activation_status == CREATED:
        session.commit()
    else:
        session.rollback()
    exit_code = (
        0 if result.activation_status in {DRY_RUN, CREATED, ALREADY_ACTIVE} else 1
    )
    return report(result), exit_code


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        with SessionLocal() as session:
            lines, exit_code = run(session, namespace)
            for line in lines:
                print(line)
            return exit_code
    except Exception as error:
        print(f"Full LIVE activation failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
