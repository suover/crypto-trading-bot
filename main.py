from crypto_trading_bot.db.database import check_database_connection


def main() -> None:
    current_time = check_database_connection()

    print("Crypto Trading Bot")
    print(f"Database connected: {current_time}")


if __name__ == "__main__":
    main()