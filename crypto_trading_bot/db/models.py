from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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


class Exchange(Base):
    __tablename__ = "exchanges"

    code: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    tradable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    default_quote_asset: Mapped[str | None] = mapped_column(String(20), nullable=True)
    default_max_order_amount: Mapped[float | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    default_daily_max_order_amount: Mapped[float | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ExchangeMarket(Base):
    __tablename__ = "exchange_markets"
    __table_args__ = (
        UniqueConstraint(
            "exchange_code", "market", name="uq_exchange_markets_exchange_market"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    exchange_code: Mapped[str] = mapped_column(
        ForeignKey("exchanges.code"), nullable=False, index=True
    )
    market: Mapped[str] = mapped_column(String(30), nullable=False)
    base_asset: Mapped[str] = mapped_column(String(20), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(20), nullable=False)
    coingecko_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ACTIVE'")
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("100")
    )
    max_order_amount_override: Mapped[float | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    daily_max_order_amount_override: Mapped[float | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    min_24h_quote_volume: Mapped[float | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    exclude_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


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
    pipeline_run_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
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


class MarketUniverseCandidate(Base):
    __tablename__ = "market_universe_candidates"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id",
            "exchange",
            "market",
            name="uq_universe_candidates_run_exchange_market",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    market: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    base_asset: Mapped[str] = mapped_column(String(20), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(20), nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Numeric(18, 9), nullable=True)
    selection_source: Mapped[str] = mapped_column(String(30), nullable=False)
    buy_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sell_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    quote_trade_value_24h: Mapped[float | None] = mapped_column(
        Numeric(30, 2), nullable=True
    )
    market_event_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    feature_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StrategyReplaySnapshot(Base):
    __tablename__ = "strategy_replay_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    pipeline_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(20), nullable=False)
    dataset_schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    policy_signature: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    policy_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    research_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    prefilter_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ranked_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    final_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StrategyReplayCandidate(Base):
    __tablename__ = "strategy_replay_candidates"
    __table_args__ = (
        UniqueConstraint(
            "strategy_replay_snapshot_id",
            "exchange",
            "market",
            name="uq_strategy_replay_candidates_snapshot_exchange_market",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    strategy_replay_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_replay_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    market: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    base_asset: Mapped[str] = mapped_column(String(20), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(20), nullable=False)
    in_prefilter: Mapped[bool] = mapped_column(Boolean, nullable=False)
    prefilter_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    held: Mapped[bool] = mapped_column(Boolean, nullable=False)
    buy_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sell_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    trading_supported: Mapped[bool] = mapped_column(Boolean, nullable=False)
    original_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_score: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 9), nullable=True
    )
    final_selected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    final_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selection_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    quote_trade_value_24h: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 2), nullable=True
    )
    feature_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StrategyReplayCandidateOutcome(Base):
    __tablename__ = "strategy_replay_candidate_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "strategy_replay_candidate_id",
            "horizon_minutes",
            name="uq_strategy_replay_candidate_outcomes_candidate_horizon",
        ),
        Index("ix_srco_candidate_id", "strategy_replay_candidate_id"),
        Index("ix_srco_snapshot_id", "strategy_replay_snapshot_id"),
        Index("ix_srco_user_id", "user_id"),
        Index(
            "ix_srco_user_target",
            "user_id",
            "target_at",
        ),
        Index(
            "ix_srco_status_target",
            "evaluation_status",
            "target_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    strategy_replay_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_replay_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    strategy_replay_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_replay_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    market: Mapped[str] = mapped_column(String(30), nullable=False)
    horizon_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reference_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    target_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reference_price: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    reference_price_source: Mapped[str] = mapped_column(String(80), nullable=False)
    end_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    end_price_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    end_price_source: Mapped[str] = mapped_column(String(50), nullable=False)
    market_return_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 12), nullable=True
    )
    evaluation_status: Mapped[str] = mapped_column(String(20), nullable=False)
    safe_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "exchange",
            "pipeline_run_id",
            name="uq_portfolio_snapshots_user_exchange_pipeline",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id"), nullable=False, unique=True
    )
    pipeline_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(20), nullable=False)
    valuation_policy_signature: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    cash_available_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    cash_locked_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    cash_total_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    priced_positions_value_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    known_total_value_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    total_value_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    positions_estimated_cost_basis_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    unrealized_pnl_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    unrealized_pnl_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    position_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unpriced_asset_count: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_cost_basis_count: Mapped[int] = mapped_column(Integer, nullable=False)
    valuation_status: Mapped[str] = mapped_column(String(20), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PortfolioPositionSnapshot(Base):
    __tablename__ = "portfolio_position_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_snapshot_id",
            "currency",
            name="uq_portfolio_positions_snapshot_currency",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    portfolio_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("account_snapshots.id"), nullable=False, index=True
    )
    market_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_snapshots.id"), nullable=True, index=True
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    market: Mapped[str | None] = mapped_column(String(30), nullable=True)
    currency: Mapped[str] = mapped_column(String(20), nullable=False)
    available_quantity: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    locked_quantity: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    total_quantity: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    avg_buy_price: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    mark_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    market_value_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    estimated_cost_basis_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    unrealized_pnl_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    unrealized_pnl_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    valuation_status: Mapped[str] = mapped_column(String(20), nullable=False)
    price_source: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
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
    universe_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_universe_candidates.id"), nullable=True, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    market: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    trade_ratio: Mapped[float | None] = mapped_column(Numeric(10, 9), nullable=True)
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


class TradeRecommendationOutcome(Base):
    __tablename__ = "trade_recommendation_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "recommendation_id",
            "horizon_minutes",
            name="uq_recommendation_outcomes_recommendation_horizon",
        ),
        Index("ix_recommendation_outcomes_user_target", "user_id", "target_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int] = mapped_column(
        ForeignKey("trade_recommendations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    market: Mapped[str] = mapped_column(String(30), nullable=False)
    horizon_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    recommendation_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    target_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reference_price: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    reference_price_source: Mapped[str] = mapped_column(String(50), nullable=False)
    end_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    end_price_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    end_price_source: Mapped[str] = mapped_column(String(50), nullable=False)
    market_return_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 12), nullable=True
    )
    action_aligned_return_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 12), nullable=True
    )
    directional_result: Mapped[str | None] = mapped_column(String(30), nullable=True)
    evaluation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )
    safe_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TradeRecommendationCandidateOutcome(Base):
    __tablename__ = "trade_recommendation_candidate_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "recommendation_id",
            "universe_candidate_id",
            "horizon_minutes",
            name="uq_candidate_outcomes_recommendation_candidate_horizon",
        ),
        Index("ix_candidate_outcomes_user_target", "user_id", "target_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int] = mapped_column(
        ForeignKey("trade_recommendations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    universe_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("market_universe_candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    market: Mapped[str] = mapped_column(String(30), nullable=False)
    horizon_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    recommendation_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    target_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(18, 9), nullable=True)
    selection_source: Mapped[str] = mapped_column(String(30), nullable=False)
    buy_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sell_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    held: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_selected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reference_price: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    end_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    end_price_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    end_price_source: Mapped[str] = mapped_column(String(50), nullable=False)
    market_return_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 12), nullable=True
    )
    evaluation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )
    safe_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
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


class OrderRetryNotification(Base):
    __tablename__ = "order_retry_notifications"

    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            name="uq_order_retry_notifications_attempt_id",
        ),
        Index(
            "ix_order_retry_notifications_delivery_next_retry",
            "delivery_status",
            "next_retry_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("order_execution_attempts.id"),
        nullable=False,
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

    telegram_chat_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    retry_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    delivery_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'PENDING'"),
    )

    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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


class OperationalAlert(Base):
    __tablename__ = "operational_alerts"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_operational_alerts_dedup_key"),
        CheckConstraint(
            "alert_type IN ('PIPELINE_FAILURE', 'STALE_LIVE_ORDER')",
            name="ck_operational_alerts_type",
        ),
        CheckConstraint(
            "severity IN ('WARNING', 'CRITICAL')",
            name="ck_operational_alerts_severity",
        ),
        CheckConstraint(
            "delivery_status IN ('PENDING', 'SENT', 'FAILED')",
            name="ck_operational_alerts_delivery_status",
        ),
        CheckConstraint(
            "delivery_attempt_count >= 0",
            name="ck_operational_alerts_attempt_count",
        ),
        Index(
            "ix_operational_alerts_delivery_due",
            "delivery_status",
            "next_retry_at",
        ),
        Index(
            "ix_operational_alerts_type_resolved",
            "alert_type",
            "resolved_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(30), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    pipeline_run_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    analysis_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_runs.id"), nullable=True, index=True
    )
    order_log_id: Mapped[int | None] = mapped_column(
        ForeignKey("order_logs.id"), nullable=True, index=True
    )
    recommendation_id: Mapped[int | None] = mapped_column(
        ForeignKey("trade_recommendations.id"), nullable=True, index=True
    )
    error_category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    http_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    safe_message: Mapped[str] = mapped_column(Text, nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(255), nullable=False)
    delivery_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PENDING'")
    )
    delivery_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
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
    executed_quantity: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    executed_funds_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    average_execution_price: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    paid_fee: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    remaining_quantity: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    trades_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    execution_synced_at: Mapped[datetime | None] = mapped_column(
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


class OrderFill(Base):
    __tablename__ = "order_fills"
    __table_args__ = (
        UniqueConstraint(
            "order_log_id",
            "exchange_trade_id",
            name="uq_order_fills_order_log_exchange_trade",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_log_id: Mapped[int] = mapped_column(
        ForeignKey("order_logs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exchange_trade_id: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    funds_krw: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    side: Mapped[str | None] = mapped_column(String(20), nullable=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AccountActivity(Base):
    __tablename__ = "account_activities"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "exchange",
            "source_type",
            "exchange_activity_id",
            name="uq_account_activities_owner_source_activity",
        ),
        CheckConstraint(
            "source_type IN ('UPBIT_CLOSED_ORDER', 'UPBIT_DEPOSIT', "
            "'UPBIT_WITHDRAWAL')",
            name="ck_account_activities_source_type",
        ),
        CheckConstraint(
            "activity_type IN ('ORDER', 'DEPOSIT', 'WITHDRAWAL')",
            name="ck_account_activities_activity_type",
        ),
        CheckConstraint(
            "origin IN ('BOT', 'EXTERNAL', 'ACCOUNT_EXTERNAL')",
            name="ck_account_activities_origin",
        ),
        CheckConstraint(
            "cash_flow_direction IS NULL OR cash_flow_direction IN ('IN', 'OUT')",
            name="ck_account_activities_cash_flow_direction",
        ),
        Index(
            "ix_account_activities_owner_occurred",
            "user_id",
            "exchange",
            "occurred_at",
        ),
        Index(
            "ix_account_activities_type_occurred",
            "activity_type",
            "occurred_at",
        ),
        Index(
            "ix_account_activities_origin_type_occurred",
            "origin",
            "activity_type",
            "occurred_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    activity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    origin: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange_activity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    market: Mapped[str | None] = mapped_column(String(30), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    side: Mapped[str | None] = mapped_column(String(20), nullable=True)
    order_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    identifier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    executed_quantity: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    executed_funds_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    paid_fee: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    fee_currency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cash_flow_direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AccountActivitySyncState(Base):
    __tablename__ = "account_activity_sync_states"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "exchange",
            "source_type",
            name="uq_account_activity_sync_states_owner_source",
        ),
        CheckConstraint(
            "sync_status IN ('NEVER_SYNCED', 'COMPLETE', 'FAILED', 'OUT_OF_SCOPE')",
            name="ck_account_activity_sync_states_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    sync_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'NEVER_SYNCED'")
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    coverage_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    coverage_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_safe_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AccountCashFlowValuation(Base):
    __tablename__ = "account_cash_flow_valuations"
    __table_args__ = (
        CheckConstraint(
            "direction IS NULL OR direction IN ('IN', 'OUT')",
            name="ck_cash_flow_valuations_direction",
        ),
        CheckConstraint(
            "valuation_status IN ('COMPLETE', 'PARTIAL')",
            name="ck_cash_flow_valuations_status",
        ),
        Index(
            "ix_cash_flow_valuations_owner_event",
            "user_id",
            "exchange",
            "event_time",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_activity_id: Mapped[int] = mapped_column(
        ForeignKey("account_activities.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    native_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    event_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valuation_price_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    cash_flow_value_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    price_source: Mapped[str] = mapped_column(String(50), nullable=False)
    valuation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )
    safe_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    valued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PortfolioPerformanceSnapshot(Base):
    __tablename__ = "portfolio_performance_snapshots"
    __table_args__ = (
        CheckConstraint(
            "performance_status IN ('BASELINE', 'COMPLETE', 'PARTIAL')",
            name="ck_portfolio_performance_status",
        ),
        CheckConstraint(
            "return_method = 'MODIFIED_DIETZ'",
            name="ck_portfolio_performance_return_method",
        ),
        Index(
            "ix_portfolio_performance_owner_period",
            "user_id",
            "exchange",
            "period_end_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    portfolio_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    previous_portfolio_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("portfolio_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    period_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    period_end_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    start_value_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    end_value_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    external_inflow_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    external_outflow_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    net_external_flow_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    return_method: Mapped[str] = mapped_column(String(30), nullable=False)
    period_return_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    cumulative_return_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    performance_index: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    high_water_mark_index: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    high_water_mark_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    drawdown_index: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    drawdown_krw: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    drawdown_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    max_drawdown_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    performance_status: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )
    safe_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class BotInventoryLot(Base):
    __tablename__ = "bot_inventory_lots"
    __table_args__ = (
        UniqueConstraint(
            "source_buy_order_log_id", name="uq_bot_inventory_lots_source_buy_order"
        ),
        CheckConstraint(
            "acquired_quantity > 0 AND remaining_quantity >= 0",
            name="ck_bot_inventory_lots_quantities",
        ),
        CheckConstraint(
            "gross_buy_funds_krw > 0 AND buy_fee_krw >= 0",
            name="ck_bot_inventory_lots_buy_values",
        ),
        CheckConstraint(
            "original_cost_basis_krw > 0 AND remaining_cost_basis_krw >= 0",
            name="ck_bot_inventory_lots_cost_basis",
        ),
        Index(
            "ix_bot_inventory_lots_scope_fifo",
            "user_id",
            "exchange",
            "market",
            "opened_at",
            "source_buy_order_log_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    market: Mapped[str] = mapped_column(String(30), nullable=False)
    source_buy_order_log_id: Mapped[int] = mapped_column(
        ForeignKey("order_logs.id", ondelete="CASCADE"), nullable=False
    )
    acquired_quantity: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    remaining_quantity: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    gross_buy_funds_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    buy_fee_krw: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    original_cost_basis_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    remaining_cost_basis_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    unit_cost_basis_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class BotSellRealization(Base):
    __tablename__ = "bot_sell_realizations"
    __table_args__ = (
        UniqueConstraint(
            "source_sell_order_log_id",
            name="uq_bot_sell_realizations_source_sell_order",
        ),
        CheckConstraint(
            "status IN ('FULLY_MATCHED', 'PARTIALLY_MATCHED', 'UNMATCHED')",
            name="ck_bot_sell_realizations_status",
        ),
        CheckConstraint(
            "sold_quantity > 0 AND matched_quantity >= 0 AND unmatched_quantity >= 0 "
            "AND matched_quantity + unmatched_quantity = sold_quantity",
            name="ck_bot_sell_realizations_quantities",
        ),
        CheckConstraint(
            "gross_sell_proceeds_krw > 0 AND sell_fee_krw >= 0",
            name="ck_bot_sell_realizations_sell_values",
        ),
        Index(
            "ix_bot_sell_realizations_scope_order",
            "user_id",
            "exchange",
            "market",
            "source_sell_order_log_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    market: Mapped[str] = mapped_column(String(30), nullable=False)
    source_sell_order_log_id: Mapped[int] = mapped_column(
        ForeignKey("order_logs.id", ondelete="CASCADE"), nullable=False
    )
    sold_quantity: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    gross_sell_proceeds_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    sell_fee_krw: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    matched_quantity: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    unmatched_quantity: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    recognized_gross_proceeds_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    recognized_sell_fee_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    recognized_net_proceeds_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    recognized_cost_basis_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    recognized_realized_pnl_krw: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    attribution_method: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'BOT_FIFO'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class BotPnlMatch(Base):
    __tablename__ = "bot_pnl_matches"
    __table_args__ = (
        UniqueConstraint(
            "sell_realization_id", "buy_lot_id", name="uq_bot_pnl_matches_sell_lot"
        ),
        CheckConstraint(
            "matched_quantity > 0 AND allocated_buy_cost_basis_krw >= 0",
            name="ck_bot_pnl_matches_quantity_cost",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sell_realization_id: Mapped[int] = mapped_column(
        ForeignKey("bot_sell_realizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    buy_lot_id: Mapped[int] = mapped_column(
        ForeignKey("bot_inventory_lots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    matched_quantity: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    allocated_buy_cost_basis_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    allocated_sell_gross_proceeds_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    allocated_sell_fee_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    allocated_sell_net_proceeds_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    realized_pnl_krw: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BotTradingPnlSummary(Base):
    __tablename__ = "bot_trading_pnl_summaries"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "exchange", name="uq_bot_trading_pnl_summaries_user_exchange"
        ),
        CheckConstraint(
            "accounting_status IN ('COMPLETE', 'PARTIAL')",
            name="ck_bot_trading_pnl_summaries_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    processed_order_count: Mapped[int] = mapped_column(Integer, nullable=False)
    processed_buy_order_count: Mapped[int] = mapped_column(Integer, nullable=False)
    processed_sell_order_count: Mapped[int] = mapped_column(Integer, nullable=False)
    gross_buy_funds_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    gross_sell_funds_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    total_buy_fees_krw: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    total_sell_fees_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    total_fees_krw: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    recognized_sell_proceeds_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    recognized_cost_basis_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    recognized_realized_pnl_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    recognized_realized_return_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    open_bot_cost_basis_krw: Mapped[Decimal] = mapped_column(
        Numeric(30, 10), nullable=False
    )
    open_bot_lot_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fully_matched_sell_count: Mapped[int] = mapped_column(Integer, nullable=False)
    partially_matched_sell_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unmatched_sell_count: Mapped[int] = mapped_column(Integer, nullable=False)
    winning_sell_count: Mapped[int] = mapped_column(Integer, nullable=False)
    losing_sell_count: Mapped[int] = mapped_column(Integer, nullable=False)
    breakeven_sell_count: Mapped[int] = mapped_column(Integer, nullable=False)
    win_rate_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    incomplete_order_count: Mapped[int] = mapped_column(Integer, nullable=False)
    accounting_status: Mapped[str] = mapped_column(String(20), nullable=False)
    source_order_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    source_last_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
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
