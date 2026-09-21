import pytest

from crypto_trading_bot.db.postgres_advisory_lock import (
    POSTGRES_BIGINT_MAX,
    POSTGRES_BIGINT_MIN,
    PostgresAdvisoryLock,
)


def test_postgres_advisory_lock_allows_only_one_holder() -> None:
    lock_key = 2026063001

    first_lock = PostgresAdvisoryLock(
        lock_key=lock_key,
    )

    second_lock = PostgresAdvisoryLock(
        lock_key=lock_key,
    )

    try:
        first_acquired = first_lock.acquire()
        second_acquired = second_lock.acquire()

        assert first_acquired is True
        assert second_acquired is False

        first_lock.release()

        second_acquired_after_release = second_lock.acquire()

        assert second_acquired_after_release is True

    finally:
        first_lock.release()
        second_lock.release()


@pytest.mark.parametrize(
    "lock_key",
    [
        POSTGRES_BIGINT_MIN - 1,
        POSTGRES_BIGINT_MAX + 1,
    ],
)
def test_postgres_advisory_lock_rejects_out_of_range_key(
    lock_key: int,
) -> None:
    with pytest.raises(ValueError):
        PostgresAdvisoryLock(
            lock_key=lock_key,
        )
