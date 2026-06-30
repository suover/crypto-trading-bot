from types import TracebackType

from sqlalchemy import text
from sqlalchemy.engine import Connection

from crypto_trading_bot.db.database import engine


POSTGRES_BIGINT_MIN = -(2**63)
POSTGRES_BIGINT_MAX = 2**63 - 1


class PostgresAdvisoryLock:
    def __init__(
        self,
        lock_key: int,
    ) -> None:
        self.lock_key = lock_key
        self.connection: Connection | None = None
        self.acquired = False

        self._validate_lock_key(lock_key)

    def acquire(self) -> bool:
        if self.connection is not None:
            raise RuntimeError(
                "Advisory lock connection already exists. "
                "Release the current lock before acquiring again."
            )

        connection = engine.connect()

        try:
            acquired = bool(
                connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {
                        "lock_key": self.lock_key,
                    },
                ).scalar_one()
            )

            connection.commit()

        except Exception:
            connection.rollback()
            connection.close()
            raise

        if not acquired:
            connection.close()
            return False

        self.connection = connection
        self.acquired = True

        return True

    def release(self) -> None:
        if self.connection is None:
            self.acquired = False
            return

        connection = self.connection

        try:
            if self.acquired:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {
                        "lock_key": self.lock_key,
                    },
                )

                connection.commit()

        except Exception:
            connection.rollback()

        finally:
            connection.close()
            self.connection = None
            self.acquired = False

    def __enter__(self) -> "PostgresAdvisoryLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    @staticmethod
    def _validate_lock_key(
        lock_key: int,
    ) -> None:
        if not (POSTGRES_BIGINT_MIN <= lock_key <= POSTGRES_BIGINT_MAX):
            raise ValueError(
                f"lock_key must fit in PostgreSQL bigint range. lock_key={lock_key}"
            )
