import argparse

from crypto_trading_bot.services.full_live_policy_termination_service import (
    ALREADY_TERMINATED,
    DRY_RUN,
    REPORT_TYPE,
    TERMINATED,
    FullLivePolicyTerminationService,
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
        description="Preview or stop an exact Full LIVE ranking activation."
    )
    parser.add_argument("--activation-id", type=_positive_integer, required=True)
    parser.add_argument("--user-id", type=_positive_integer, required=True)
    parser.add_argument("--exchange", required=True)
    parser.add_argument("--quote-asset", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-activation-signature")
    parser.add_argument("--reason")
    namespace = parser.parse_args(args)
    if namespace.apply and (
        not namespace.expected_activation_signature or not namespace.reason
    ):
        parser.error("--apply requires --expected-activation-signature and --reason")
    if not namespace.apply and (
        namespace.expected_activation_signature or namespace.reason
    ):
        parser.error("signature and reason are only valid with --apply")
    return namespace


def _bool(value: bool) -> str:
    return str(value).lower()


def report(result) -> list[str]:
    activation = result.activation
    termination = result.termination
    return [
        f"report_type={REPORT_TYPE}",
        f"activation_id={result.activation_id}",
        f"user_id={result.user_id}",
        f"exchange={result.exchange}",
        f"quote_asset={result.quote_asset}",
        f"activation_signature={getattr(activation, 'activation_signature', None)}",
        f"termination_id={getattr(termination, 'id', None)}",
        f"termination_status={result.termination_status}",
        f"termination_signature={getattr(termination, 'termination_signature', None)}",
        f"database_write={_bool(result.database_write)}",
        f"live_policy_change={_bool(result.live_policy_change)}",
        f"live_order_change={_bool(result.live_order_change)}",
        f"external_calls={_bool(result.external_calls)}",
        f"safe_reason={result.safe_reason}",
    ]


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    service = FullLivePolicyTerminationService(session)
    kwargs = {
        "activation_id": namespace.activation_id,
        "user_id": namespace.user_id,
        "exchange": namespace.exchange,
        "quote_asset": namespace.quote_asset,
    }
    result = (
        service.terminate(
            **kwargs,
            expected_activation_signature=namespace.expected_activation_signature,
            reason=namespace.reason,
        )
        if namespace.apply
        else service.preview(**kwargs)
    )
    if result.termination_status == TERMINATED:
        session.commit()
    else:
        session.rollback()
    exit_code = (
        0
        if result.termination_status in {DRY_RUN, TERMINATED, ALREADY_TERMINATED}
        else 1
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
        print(f"Full LIVE stop failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
