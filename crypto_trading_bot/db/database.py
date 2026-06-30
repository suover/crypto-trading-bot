from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from crypto_trading_bot.config.settings import get_settings


settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


def check_database_connection() -> str:
    with engine.connect() as connection:
        result = connection.execute(text("SELECT now()"))
        return str(result.scalar_one())
