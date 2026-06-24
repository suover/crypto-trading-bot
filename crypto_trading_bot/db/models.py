from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from crypto_trading_bot.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    telegram_chat_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    run_type: Mapped[str] = mapped_column(String(30), nullable=False)
    trading_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    market: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    current_price: Mapped[float | None] = mapped_column(Numeric(30, 10), nullable=True)
    change_rate: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Numeric(30, 10), nullable=True)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    currency: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    balance: Mapped[float | None] = mapped_column(Numeric(30, 10), nullable=True)
    locked: Mapped[float | None] = mapped_column(Numeric(30, 10), nullable=True)
    avg_buy_price: Mapped[float | None] = mapped_column(Numeric(30, 10), nullable=True)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class TradeRecommendation(Base):
    __tablename__ = "trade_recommendations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id"),
        nullable=False,
        index=True,
    )
    market_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_snapshots.id"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    market: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_amount_krw: Mapped[float | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    recommended_quantity: Mapped[float | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    ai_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int] = mapped_column(
        ForeignKey("trade_recommendations.id"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    callback_token: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class OrderExecutionAttempt(Base):
    __tablename__ = "order_execution_attempts"

    __table_args__ = (
        UniqueConstraint(
            "recommendation_id",
            "trading_mode",
            "attempt_number",
            name=("uq_order_execution_attempts_recommendation_mode_number"),
        ),
        Index(
            "ix_order_execution_attempts_status_next_retry_at",
            "status",
            "next_retry_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    recommendation_id: Mapped[int] = mapped_column(
        ForeignKey("trade_recommendations.id"),
        nullable=False,
        index=True,
    )
    approval_request_id: Mapped[int] = mapped_column(
        ForeignKey("approval_requests.id"),
        nullable=False,
        index=True,
    )
    order_log_id: Mapped[int | None] = mapped_column(
        ForeignKey("order_logs.id"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    trading_mode: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class OrderLog(Base):
    __tablename__ = "order_logs"

    __table_args__ = (
        UniqueConstraint(
            "recommendation_id",
            name="uq_order_logs_recommendation_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int] = mapped_column(
        ForeignKey("trade_recommendations.id"),
        nullable=False,
    )
    approval_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("approval_requests.id"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    trading_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    market: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(20), nullable=False)
    order_type: Mapped[str] = mapped_column(String(30), nullable=False)
    amount_krw: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
    quantity: Mapped[float | None] = mapped_column(Numeric(30, 10), nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(30, 10), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    exchange_order_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class MarketCandle(Base):
    __tablename__ = "market_candles"

    __table_args__ = (
        UniqueConstraint(
            "exchange",
            "market",
            "candle_type",
            "candle_unit",
            "candle_at",
            name="uq_market_candles_exchange_market_type_unit_at",
        ),
        Index("ix_market_candles_market_candle_at", "market", "candle_at"),
        Index("ix_market_candles_exchange_market", "exchange", "market"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    market: Mapped[str] = mapped_column(String(30), nullable=False)

    candle_type: Mapped[str] = mapped_column(String(30), nullable=False)
    candle_unit: Mapped[int] = mapped_column(Integer, nullable=False)
    candle_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    opening_price: Mapped[float] = mapped_column(Numeric(24, 10), nullable=False)
    high_price: Mapped[float] = mapped_column(Numeric(24, 10), nullable=False)
    low_price: Mapped[float] = mapped_column(Numeric(24, 10), nullable=False)
    trade_price: Mapped[float] = mapped_column(Numeric(24, 10), nullable=False)

    candle_acc_trade_price: Mapped[float] = mapped_column(
        Numeric(24, 10),
        nullable=False,
    )
    candle_acc_trade_volume: Mapped[float] = mapped_column(
        Numeric(24, 10),
        nullable=False,
    )

    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
