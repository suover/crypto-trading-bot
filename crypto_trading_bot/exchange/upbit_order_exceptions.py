from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UpbitSafeError:
    error_type: str
    operation: str
    status_code: int | None = None
    upbit_error_name: str | None = None
    message: str = "Upbit order operation failed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.error_type,
            "operation": self.operation,
            "status_code": self.status_code,
            "upbit_error_name": self.upbit_error_name,
            "message": self.message,
        }


class UpbitOrderOperationError(Exception):
    def __init__(self, safe_error: UpbitSafeError) -> None:
        self.safe_error = safe_error
        super().__init__(
            f"{safe_error.operation}: {safe_error.message}"
            + (
                f" (status_code={safe_error.status_code})"
                if safe_error.status_code is not None
                else ""
            )
        )


class UpbitOrderNotFoundError(UpbitOrderOperationError):
    pass


class UpbitOrderRejectedError(UpbitOrderOperationError):
    pass


class UpbitOrderAmbiguousError(UpbitOrderOperationError):
    pass
