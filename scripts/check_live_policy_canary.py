import argparse

from crypto_trading_bot.services.live_policy_canary_service import STATUS_REPORT_TYPE
from crypto_trading_bot.services.live_ranking_policy_resolver import (
    LiveRankingPolicyResolver,
)


def parse_arguments(args=None):
    parser = argparse.ArgumentParser(
        description="Inspect Limited LIVE Canary v1-A without reserving a run."
    )
    parser.add_argument("--user-name", default="Minsu")
    return parser.parse_args(args)


def report(result):
    activation = result.activation
    approval = result.promotion_approval
    return [
        f"report_type={STATUS_REPORT_TYPE}",
        f"mode={result.mode}",
        f"active_canary_count={result.active_canary_count}",
        f"activation_id={getattr(activation, 'id', None)}",
        f"promotion_approval_id={getattr(approval, 'id', None)}",
        f"candidate_id={getattr(approval, 'candidate_id', None)}",
        f"baseline_policy_signature={result.baseline_policy_signature}",
        f"canary_policy_signature={result.canary_policy_signature}",
        f"started_at={getattr(activation, 'started_at', None)}",
        f"expires_at={getattr(activation, 'expires_at', None)}",
        f"used_analysis_run_count={result.used_analysis_run_count}",
        f"max_analysis_runs={result.max_analysis_runs}",
        f"fallback_reason={result.fallback_reason}",
        "database_write=false",
        "external_calls=false",
    ]


def run(session, namespace):
    result = LiveRankingPolicyResolver(session).inspect(user_name=namespace.user_name)
    session.rollback()
    return report(result)


def main(args=None):
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        with SessionLocal() as session:
            for line in run(session, namespace):
                print(line)
        return 0
    except Exception as error:
        print(f"Canary status failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
