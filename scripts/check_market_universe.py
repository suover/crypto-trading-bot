from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.services.market_universe_service import MarketUniverseService


def check_market_universe() -> None:
    with SessionLocal() as session:
        result = MarketUniverseService(session).build_and_persist()
        print(f"exchange={result.exchange}")
        print(f"quote_asset={result.quote_asset}")
        print(f"total_market_count={result.total_market_count}")
        print(f"warning_excluded_count={result.warning_excluded_count}")
        print(f"caution_excluded_count={result.caution_excluded_count}")
        print(f"blocklist_excluded_count={result.blocklist_excluded_count}")
        print(f"liquidity_excluded_count={result.liquidity_excluded_count}")
        print(f"liquidity_prefilter_count={result.prefilter_count}")
        print(f"final_ranked_count={result.ranked_count}")
        print(f"holdings_added_count={result.holdings_added_count}")
        print("final_candidates:")
        for row in result.candidates:
            timeframes = row.feature_data.get("timeframes", {})
            trends = ", ".join(
                f"{name}:{value.get('trend_label')}/{value.get('data_quality')}"
                for name, value in timeframes.items()
            )
            print(
                f"- {row.market} rank={row.rank} source={row.selection_source} "
                f"buy_eligible={row.buy_eligible} sell_eligible={row.sell_eligible} "
                f"trade_value_24h_krw={row.quote_trade_value_24h} score={row.score} "
                f"timeframes=[{trends}]"
            )


if __name__ == "__main__":
    check_market_universe()
