import argparse

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import User


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a user and print the numeric ID used by runtime settings."
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Display name. It is not used as the system identity.",
    )
    return parser.parse_args(args)


def seed_default_user(name: str) -> User:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("--name must not be empty")

    with SessionLocal() as session:
        user = User(
            name=normalized_name,
            telegram_chat_id=None,
        )

        session.add(user)
        session.commit()
        session.refresh(user)

        print(f"user_id={user.id}")
        print(f"name={user.name}")
        print("created=true")
        return user


if __name__ == "__main__":
    arguments = parse_arguments()
    seed_default_user(arguments.name)
