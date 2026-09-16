import argparse

from crypto_trading_bot.services.live_ranking_policy_resolver import (
    LiveRankingPolicyResolver,
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
        description="Inspect the effective LIVE ranking policy without writes."
    )
    parser.add_argument("--user-id", type=_positive_integer, required=True)
    parser.add_argument("--exchange", required=True)
    parser.add_argument("--quote-asset", required=True)
    return parser.parse_args(args)


def run(session, namespace: argparse.Namespace) -> list[str]:
    resolution = LiveRankingPolicyResolver(session).inspect(
        user_id=namespace.user_id,
        exchange=namespace.exchange,
        quote_asset=namespace.quote_asset,
    )
    return [
        f"user_id={resolution.user_id}",
        f"exchange={resolution.exchange}",
        f"quote_asset={resolution.quote_asset}",
        f"mode={resolution.mode}",
        f"baseline_policy_signature={resolution.baseline_policy_signature}",
        f"effective_policy_signature={resolution.effective_policy_signature}",
        f"activation_id={resolution.full_live_activation_id}",
        f"promotion_approval_id={resolution.promotion_approval_id}",
        f"scenario_name={resolution.scenario_name}",
        f"activated_at={resolution.activated_at}",
        f"terminated={str(resolution.terminated).lower()}",
        "database_write=false",
        "external_calls=false",
        "live_order_change=false",
    ]


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        with SessionLocal() as session:
            for line in run(session, namespace):
                print(line)
        return 0
    except Exception as error:
        print(
            f"Live ranking policy inspection failed. error_type={type(error).__name__}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
