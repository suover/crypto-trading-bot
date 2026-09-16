import pytest

from scripts.activate_full_live_policy import parse_arguments as parse_activation
from scripts.check_live_ranking_policy import parse_arguments as parse_status
from scripts.stop_full_live_policy import parse_arguments as parse_stop


def test_activation_cli_is_dry_run_by_default_and_apply_requires_signature() -> None:
    namespace = parse_activation(
        [
            "--promotion-approval-id",
            "7",
            "--user-id",
            "3",
            "--exchange",
            "UPBIT",
            "--quote-asset",
            "KRW",
        ]
    )
    assert namespace.apply is False
    with pytest.raises(SystemExit):
        parse_activation(
            [
                "--promotion-approval-id",
                "7",
                "--user-id",
                "3",
                "--exchange",
                "UPBIT",
                "--quote-asset",
                "KRW",
                "--apply",
            ]
        )


def test_stop_cli_is_dry_run_by_default_and_apply_requires_signature_reason() -> None:
    namespace = parse_stop(
        [
            "--activation-id",
            "11",
            "--user-id",
            "3",
            "--exchange",
            "UPBIT",
            "--quote-asset",
            "KRW",
        ]
    )
    assert namespace.apply is False
    with pytest.raises(SystemExit):
        parse_stop(
            [
                "--activation-id",
                "11",
                "--user-id",
                "3",
                "--exchange",
                "UPBIT",
                "--quote-asset",
                "KRW",
                "--apply",
                "--expected-activation-signature",
                "signature",
            ]
        )


@pytest.mark.parametrize("parser", (parse_activation, parse_stop, parse_status))
def test_full_live_clis_reject_non_positive_user_id(parser) -> None:
    identifier = (
        ["--promotion-approval-id", "7"]
        if parser is parse_activation
        else ["--activation-id", "11"]
        if parser is parse_stop
        else []
    )
    with pytest.raises(SystemExit):
        parser(
            [
                *identifier,
                "--user-id",
                "0",
                "--exchange",
                "UPBIT",
                "--quote-asset",
                "KRW",
            ]
        )
