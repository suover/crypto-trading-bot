from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import User


class RuntimeUserConfigurationError(ValueError):
    """Raised when the explicitly configured runtime user is unsafe to use."""


def validate_user_id(user_id: object) -> int:
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise RuntimeUserConfigurationError("user_id must be a positive integer")
    return user_id


def require_trading_user_id(configured_user_id: int | None) -> int:
    if configured_user_id is None:
        raise RuntimeUserConfigurationError("TRADING_USER_ID is required")
    return validate_user_id(configured_user_id)


class RuntimeUserResolver:
    """Resolve one explicit runtime user without name or single-row fallbacks."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def resolve(self, user_id: object) -> User:
        normalized_user_id = validate_user_id(user_id)
        user = self.session.get(User, normalized_user_id)
        if user is None:
            raise RuntimeUserConfigurationError("configured runtime user was not found")
        if not user.is_active:
            raise RuntimeUserConfigurationError("configured runtime user is inactive")
        return user

    def resolve_configured(self, configured_user_id: int | None) -> User:
        return self.resolve(require_trading_user_id(configured_user_id))


__all__ = [
    "RuntimeUserConfigurationError",
    "RuntimeUserResolver",
    "require_trading_user_id",
    "validate_user_id",
]
