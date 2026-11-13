"""Additional rows in the disposable Backend Testcontainers database only."""

from datetime import timedelta
from decimal import Decimal

from crypto_trading_bot.db.models import (
    User,
    AnalysisRun,
    MarketUniverseCandidate,
    TradeRecommendation,
    ApprovalRequest,
    OrderLog,
    OrderExecutionAttempt,
)

BIG = 9007199254741093


def prepare_trade_analysis(session, time):
    session.add_all(User(id=i, name=f"analysis-fixture-{i}") for i in (20, 21))
    session.flush()
    for uid, user, pipeline, kind, status in [
        (7001, 20, "pipeline-5001", "MARKET_UNIVERSE", "SUCCESS"),
        (7002, 20, "pipeline-5001", "MARKET_UNIVERSE", "SUCCESS"),
        (7101, 20, "wrong-pipeline", "MARKET_UNIVERSE", "SUCCESS"),
        (7102, 20, "pipeline-5016", "OTHER", "SUCCESS"),
        (7103, 20, "pipeline-5017", "MARKET_UNIVERSE", "FAILED"),
        (7104, 21, "pipeline-5019", "MARKET_UNIVERSE", "SUCCESS"),
        (7105, 20, "pipeline-5001", "MARKET_UNIVERSE", "SUCCESS"),
        (7106, 20, "pipeline-5001", "MARKET_UNIVERSE", "SUCCESS"),
    ]:
        session.add(
            AnalysisRun(
                id=uid,
                user_id=user,
                pipeline_run_id=pipeline,
                run_type=kind,
                status=status,
                trading_mode="AI_APPROVAL",
            )
        )
    session.flush()
    features = {
        "data_quality": "PARTIAL",
        "timeframes": {
            "15m": {
                "data_quality": "SUFFICIENT",
                "candle_count": 50,
                "trend_label": "과열 주의",
                "rsi_14": "71.1234567890123456789012345678",
                "latest_price": "12345678901234567890.1234567890",
                "macd": "-0.0000000001",
                "private_extra": "fixture-private-audit",
            },
            "1h": {
                "data_quality": "SUFFICIENT",
                "candle_count": 50,
                "trend_label": "상승 우위",
            },
            "4h": {"data_quality": "UNAVAILABLE"},
            "1d": {"data_quality": "INSUFFICIENT", "candle_count": 3},
        },
        "position": {"private": "fixture-private-audit"},
    }
    for cid, run, user, exchange, quote, market in [
        (8001, 7001, 20, "UPBIT", "KRW", "KRW-BTC"),
        (8002, 7001, 20, "UPBIT", "KRW", "KRW-ETH"),
        (8201, 7002, 20, "UPBIT", "KRW", "KRW-SOL"),
        (8003, 7001, 21, "UPBIT", "KRW", "KRW-XRP"),
        (8004, 7001, 20, "BINANCE", "KRW", "KRW-XRP"),
        (8005, 7001, 20, "UPBIT", "BTC", "BTC-XRP"),
        (8101, 7101, 20, "UPBIT", "KRW", "KRW-BTC"),
        (8102, 7105, 21, "UPBIT", "KRW", "KRW-BTC"),
        (8103, 7001, 20, "BINANCE", "KRW", "KRW-BTC"),
        (8104, 7106, 20, "UPBIT", "BTC", "KRW-BTC"),
        (8105, 7102, 20, "UPBIT", "KRW", "KRW-BTC"),
        (8106, 7103, 20, "UPBIT", "KRW", "KRW-BTC"),
        (8107, 7104, 20, "UPBIT", "KRW", "KRW-BTC"),
    ]:
        session.add(
            MarketUniverseCandidate(
                id=cid,
                analysis_run_id=run,
                user_id=user,
                exchange=exchange,
                quote_asset=quote,
                base_asset=market.split("-")[1],
                market=market,
                rank=1 if cid != 8002 else None,
                score=Decimal("0.123456789"),
                selection_source="HELD" if cid == 8002 else "RANKED",
                buy_eligible=cid != 8002,
                sell_eligible=True,
                quote_trade_value_24h=Decimal("1234567890123456789012345678.12"),
                feature_data=features,
                created_at=time,
            )
        )
    session.flush()
    references = {
        5001: 8001,
        5005: 8101,
        5006: 8102,
        5007: 8103,
        5008: 8104,
        5009: 8002,
        5016: 8105,
        5017: 8106,
        5018: 8001,
        5019: 8107,
    }
    for rid in [*range(5001, 5020), BIG]:
        user = 21 if rid == 5011 else 20
        pipeline = None if rid in (5004, 5018) else f"pipeline-{rid}"
        if rid in (5006, 5007, 5008, 5009):
            pipeline = "pipeline-5001"
        aid = rid + 1000
        session.add(
            AnalysisRun(
                id=aid,
                user_id=21 if rid == 5010 else user,
                pipeline_run_id=pipeline,
                run_type="AI_TRADE_RECOMMENDATION",
                trading_mode="AI_APPROVAL",
                status="SUCCESS",
                started_at=time,
                finished_at=time + timedelta(minutes=1),
                error_message="fixture-private-audit",
            )
        )
        session.flush()
        session.add(
            TradeRecommendation(
                id=rid,
                analysis_run_id=aid,
                universe_candidate_id=references.get(rid),
                user_id=user,
                exchange="BINANCE" if rid == 5012 else "UPBIT",
                market={5013: "BTC-ETH", 5014: "XKRW-BTC", 5015: "KRW-"}.get(
                    rid, "KRW-BTC"
                ),
                action="HOLD" if rid == 5003 else "SELL" if rid == 5002 else "BUY",
                reason="Final decision after application safety checks",
                trade_ratio=None if rid == 5003 else Decimal("0.123456789"),
                confidence=Decimal("0.9876"),
                recommended_amount_krw=Decimal("10000.12"),
                recommended_quantity=Decimal("0.0000000001"),
                ai_model="fixture-model",
                status="HOLD" if rid == 5003 else "APPROVED",
                ai_response={"private": "fixture-private-audit"},
                created_at=time,
                updated_at=time,
            )
        )
    session.flush()
    for pid, rid, user, status in [
        (9501, 5001, 20, "APPROVED"),
        (9502, 5002, 20, "APPROVED"),
        (9503, 5004, 20, "EXPIRED"),
        (9504, 5001, 21, "APPROVED"),
    ]:
        session.add(
            ApprovalRequest(
                id=pid,
                recommendation_id=rid,
                user_id=user,
                status=status,
                callback_token=f"fixture-private-callback-{pid}",
                telegram_chat_id=111111,
                telegram_message_id=222222,
                expires_at=time + timedelta(minutes=5),
                approved_at=time if status == "APPROVED" else None,
                created_at=time,
                updated_at=time,
            )
        )
    session.flush()
    for oid, rid, pid, user, exchange, market, mode, status in [
        (9701, 5001, 9501, 20, "UPBIT", "KRW-BTC", "LIVE", "LIVE_EXECUTED_CANCELLED"),
        (9702, 5002, 9502, 20, "UPBIT", "KRW-BTC", "LIVE", "LIVE_UNKNOWN"),
        (9703, 5004, None, 20, "UPBIT", "KRW-BTC", "MOCK", "MOCK_EXECUTED"),
        (9704, 5005, None, 21, "UPBIT", "KRW-BTC", "LIVE", "LIVE_EXECUTED"),
        (9705, 5006, None, 20, "BINANCE", "KRW-BTC", "LIVE", "LIVE_EXECUTED"),
        (9706, 5007, None, 20, "UPBIT", "KRW-ETH", "LIVE", "LIVE_EXECUTED"),
        (9707, 5008, 9502, 20, "UPBIT", "KRW-BTC", "LIVE", "LIVE_EXECUTED"),
    ]:
        session.add(
            OrderLog(
                id=oid,
                recommendation_id=rid,
                approval_request_id=pid,
                user_id=user,
                exchange=exchange,
                market=market,
                trading_mode=mode,
                side="SELL" if rid == 5002 else "BUY",
                order_type="MARKET",
                amount_krw=Decimal("10000.12"),
                quantity=Decimal("2"),
                price=Decimal("5000"),
                status=status,
                exchange_order_id="fixture-private-exchange-id",
                raw_response={"private": "fixture-private-audit"},
                error_message="fixture-private-audit",
                executed_quantity=Decimal("1") if oid == 9701 else None,
                executed_funds_krw=Decimal("1500") if oid == 9701 else None,
                average_execution_price=Decimal("1500") if oid == 9701 else None,
                paid_fee=Decimal("0") if oid == 9701 else None,
                remaining_quantity=Decimal("1") if oid == 9701 else None,
                trades_count=1 if oid == 9701 else None,
                execution_synced_at=time + timedelta(minutes=3)
                if oid == 9701
                else None,
            )
        )
    session.flush()
    for tid, rid, pid, oid, user, mode, number in [
        (9601, 5001, 9501, 9701, 20, "LIVE", 1),
        (9602, 5002, 9502, 9702, 20, "LIVE", 1),
        (9603, 5001, 9501, None, 20, "MOCK", 1),
        (9604, 5001, 9501, None, 21, "LIVE", 2),
        (9605, 5001, 9502, None, 20, "LIVE", 3),
        (9606, 5001, 9501, 9702, 20, "LIVE", 4),
        (9607, 5001, 9501, 9701, 20, "MOCK", 2),
    ]:
        session.add(
            OrderExecutionAttempt(
                id=tid,
                recommendation_id=rid,
                approval_request_id=pid,
                order_log_id=oid,
                user_id=user,
                trading_mode=mode,
                attempt_number=number,
                status="UNKNOWN" if tid == 9602 else "SUCCESS",
                error_code="UNKNOWN_ORDER" if tid == 9602 else None,
                error_message="fixture-private-audit",
                attempted_at=time,
            )
        )
