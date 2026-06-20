from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import User


def seed_default_user() -> None:
    with SessionLocal() as session:
        existing_user = session.query(User).filter(User.name == "Minsu").first()

        if existing_user is not None:
            print(f"Default user already exists. id={existing_user.id}, name={existing_user.name}")
            return

        user = User(
            name="Minsu",
            telegram_chat_id=None,
        )

        session.add(user)
        session.commit()
        session.refresh(user)

        print(f"Default user created. id={user.id}, name={user.name}")


if __name__ == "__main__":
    seed_default_user()