from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Callable

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import Settings, get_settings
from crypto_trading_bot.db.models import (
    AnalysisRun,
    ApprovalRequest,
    BotTradingPnlSummary,
    LivePolicyCanaryActivation,
    LivePolicyCanaryRun,
    MarketUniverseCandidate,
    OperationalAlert,
    OrderFill,
    OrderLog,
    PortfolioSnapshot,
    TradeRecommendation,
    TradeRecommendationOutcome,
)
from crypto_trading_bot.services.canary_trade_provenance_service import (
    BASELINE_RECOMMENDATION,
    CANARY_RECOMMENDATION,
    CanaryTradeProvenanceService,
)
from crypto_trading_bot.services.live_policy_canary_service import (
    _canonicalize,
    load_and_validate_canary_safety_binding,
    load_and_validate_live_policy_canary_run,
    validate_stored_canary_activation,
)
from crypto_trading_bot.services.live_order_execution_service import (
    LIVE_ORDER_CANCELLED_STATUS,
    LIVE_ORDER_DONE_STATUS,
    LIVE_ORDER_EXECUTED_CANCELLED_STATUS,
    LIVE_ORDER_FAILED_STATUS,
    LIVE_ORDER_PLACED_STATUS,
    LIVE_ORDER_UNKNOWN_STATUS,
    LIVE_ORDER_WAIT_STATUS,
)
from crypto_trading_bot.services.live_policy_canary_termination_service import (
    load_and_validate_canary_termination,
)


REPORT_TYPE = "LIVE_CANARY_EVIDENCE_V1"
EVIDENCE_SCHEMA_VERSION = "live-canary-evidence-v1"
NO_CANARY_ACTIVATION = "NO_CANARY_ACTIVATION"
INVALID_CANARY_EVIDENCE = "INVALID_CANARY_EVIDENCE"
NO_CANARY_RUNS = "NO_CANARY_RUNS"
EVIDENCE_AVAILABLE = "EVIDENCE_AVAILABLE"

ACTIVE = "ACTIVE"
STOPPED = "STOPPED"
EXPIRED = "EXPIRED"
EXHAUSTED = "EXHAUSTED"

_SOURCE_MODELS = {
    "canary_run_id_ceiling": LivePolicyCanaryRun,
    "recommendation_id_ceiling": TradeRecommendation,
    "approval_request_id_ceiling": ApprovalRequest,
    "order_log_id_ceiling": OrderLog,
    "order_fill_id_ceiling": OrderFill,
    "operational_alert_id_ceiling": OperationalAlert,
    "recommendation_outcome_id_ceiling": TradeRecommendationOutcome,
    "portfolio_snapshot_id_ceiling": PortfolioSnapshot,
}
_ORDER_STATUSES = (
    LIVE_ORDER_PLACED_STATUS,
    LIVE_ORDER_WAIT_STATUS,
    LIVE_ORDER_DONE_STATUS,
    LIVE_ORDER_CANCELLED_STATUS,
    LIVE_ORDER_EXECUTED_CANCELLED_STATUS,
    LIVE_ORDER_FAILED_STATUS,
    LIVE_ORDER_UNKNOWN_STATUS,
)
_TERMINAL_EXECUTION_STATUSES = {
    LIVE_ORDER_DONE_STATUS,
    LIVE_ORDER_EXECUTED_CANCELLED_STATUS,
}


class LiveCanaryEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveCanaryEvidenceReport:
    status: str
    lifecycle_state: str | None
    canary_activation_id: int
    candidate_id: int | None
    evidence_as_of: datetime
    snapshot_consistency: str
    ceilings: dict[str, int | None]
    payload: dict[str, Any]
    evidence_signature: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "report_type": REPORT_TYPE,
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "canary_activation_id": self.canary_activation_id,
            "candidate_id": self.candidate_id,
            "status": self.status,
            "lifecycle_state": self.lifecycle_state,
            "evidence_as_of": self.evidence_as_of,
            "evidence_signature": self.evidence_signature,
            "snapshot_consistency": self.snapshot_consistency,
            **self.ceilings,
            **self.payload,
        }


def live_canary_evidence_signature(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonicalize(payload), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"{EVIDENCE_SCHEMA_VERSION}:{sha256(encoded).hexdigest()}"


class LiveCanaryEvidenceService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.now_fn = now_fn

    def evaluate(self, *, canary_activation_id: int) -> LiveCanaryEvidenceReport:
        if isinstance(canary_activation_id, bool) or canary_activation_id <= 0:
            raise ValueError("canary_activation_id must be a positive integer")
        if self.session.new or self.session.dirty or self.session.deleted:
            raise LiveCanaryEvidenceError(
                "Evidence requires a clean Session without pending mutations"
            )
        consistency = self._start_snapshot()
        evidence_as_of = self._capture_evidence_as_of()
        with self.session.no_autoflush:
            ceilings = self._capture_ceilings(evidence_as_of)
            activation = self.session.scalar(
                select(LivePolicyCanaryActivation).where(
                    LivePolicyCanaryActivation.id == canary_activation_id,
                    LivePolicyCanaryActivation.created_at <= evidence_as_of,
                )
            )
            if activation is None:
                return self._finish(
                    status=NO_CANARY_ACTIVATION,
                    lifecycle=None,
                    activation_id=canary_activation_id,
                    candidate_id=None,
                    evidence_as_of=evidence_as_of,
                    consistency=consistency,
                    ceilings=ceilings,
                    payload=self._empty_payload(),
                )
            try:
                validated, _, _ = validate_stored_canary_activation(
                    self.session, activation, self.settings
                )
                binding = load_and_validate_canary_safety_binding(
                    self.session, activation
                )
                termination = load_and_validate_canary_termination(
                    self.session, activation, binding
                )
            except (ValueError, TypeError, AttributeError, KeyError) as error:
                payload = self._empty_payload()
                payload["activation_provenance"] = self._activation_payload(activation)
                payload["integrity"] = self._integrity(
                    findings=(f"ACTIVATION_LINEAGE_INVALID:{type(error).__name__}",),
                    activation=False,
                    binding=False,
                    promotion=False,
                    termination=False,
                )
                return self._finish(
                    status=INVALID_CANARY_EVIDENCE,
                    lifecycle=None,
                    activation_id=activation.id,
                    candidate_id=activation.candidate_id,
                    evidence_as_of=evidence_as_of,
                    consistency=consistency,
                    ceilings=ceilings,
                    payload=payload,
                )

            runs = tuple(
                self.session.scalars(
                    select(LivePolicyCanaryRun)
                    .where(
                        LivePolicyCanaryRun.canary_activation_id == activation.id,
                        LivePolicyCanaryRun.id
                        <= self._ceiling(ceilings, "canary_run_id_ceiling"),
                        LivePolicyCanaryRun.reserved_at <= evidence_as_of,
                    )
                    .order_by(LivePolicyCanaryRun.run_ordinal, LivePolicyCanaryRun.id)
                )
            )
            payload, findings, checks = self._collect(
                activation=activation,
                approval=validated.row,
                binding=binding,
                termination=termination,
                runs=runs,
                evidence_as_of=evidence_as_of,
                ceilings=ceilings,
            )
            status = (
                INVALID_CANARY_EVIDENCE
                if findings
                else EVIDENCE_AVAILABLE
                if runs
                else NO_CANARY_RUNS
            )
            payload["integrity"] = self._integrity(findings=findings, **checks)
            return self._finish(
                status=status,
                lifecycle=self._lifecycle(
                    activation, termination, len(runs), evidence_as_of
                ),
                activation_id=activation.id,
                candidate_id=activation.candidate_id,
                evidence_as_of=evidence_as_of,
                consistency=consistency,
                ceilings=ceilings,
                payload=payload,
            )

    def _start_snapshot(self) -> str:
        dialect = self.session.get_bind().dialect.name
        if dialect == "postgresql":
            if self.session.in_transaction():
                raise LiveCanaryEvidenceError(
                    "Evidence Session already has an active transaction"
                )
            try:
                connection = self.session.connection(
                    execution_options={"isolation_level": "REPEATABLE READ"}
                )
                connection.execute(text("SET TRANSACTION READ ONLY"))
            except (ValueError, TypeError, AttributeError, KeyError) as error:
                self.session.rollback()
                raise LiveCanaryEvidenceError(
                    "Could not establish REPEATABLE READ READ ONLY snapshot"
                ) from error
            return "REPEATABLE_READ_READ_ONLY"
        if dialect == "sqlite":
            self.session.connection()
            return "SQLITE_TRANSACTION"
        raise LiveCanaryEvidenceError(f"Unsupported evidence database: {dialect}")

    def _capture_evidence_as_of(self) -> datetime:
        if self.now_fn is not None:
            value = self.now_fn()
        elif self.session.get_bind().dialect.name == "postgresql":
            value = self.session.scalar(select(func.transaction_timestamp()))
        else:
            value = datetime.now(UTC)
        return self._utc(value, "evidence_as_of")

    def _capture_ceilings(self, evidence_as_of) -> dict[str, int | None]:
        ceilings = {}
        for name, model in _SOURCE_MODELS.items():
            statement = select(func.max(model.id))
            if hasattr(model, "created_at"):
                statement = statement.where(model.created_at <= evidence_as_of)
            ceilings[name] = self.session.scalar(statement)
        return ceilings

    def _collect(
        self,
        *,
        activation,
        approval,
        binding,
        termination,
        runs,
        evidence_as_of,
        ceilings,
    ):
        findings: list[str] = []
        checks = {
            "activation": True,
            "binding": True,
            "promotion": True,
            "termination": True,
            "runs": True,
            "recommendations": True,
            "orders": True,
            "fills": True,
            "snapshot": True,
        }
        run_rows, analyses, problems = self._run_evidence(activation, runs)
        if problems:
            checks["runs"] = False
            findings.extend(problems)
        selections, candidates, problems = self._selection_evidence(
            activation, analyses
        )
        if problems:
            checks["runs"] = False
            findings.extend(problems)
        recommendations, recommendation_rows, problems = self._recommendation_evidence(
            activation, candidates, evidence_as_of, ceilings
        )
        if problems:
            checks["recommendations"] = False
            findings.extend(problems)
        recommendation_ids = tuple(row.id for row in recommendation_rows)
        approvals = self._approval_evidence(
            recommendation_ids, evidence_as_of, ceilings
        )
        orders, order_rows, problems = self._order_evidence(
            activation, recommendation_rows, evidence_as_of, ceilings
        )
        if problems:
            checks["orders"] = False
            findings.extend(problems)
        fills, fill_summary, problems = self._fill_evidence(
            order_rows, evidence_as_of, ceilings
        )
        if problems:
            checks["fills"] = False
            findings.extend(problems)
        operational = self._operational_evidence(
            activation,
            runs,
            recommendation_ids,
            tuple(row.id for row in order_rows),
            evidence_as_of,
            ceilings,
        )
        submitted_buy = sum(
            (
                Decimal(row.amount_krw)
                for row in order_rows
                if row.side == "BUY"
                and row.amount_krw is not None
                and self._order_was_submitted(row)
            ),
            Decimal("0"),
        )
        financial = {
            "total_recommended_buy_amount_krw": recommendations["summary"][
                "total_recommended_buy_amount_krw"
            ],
            "submitted_buy_amount_krw": submitted_buy,
            **fill_summary,
            "executed_buy_quantity": fill_summary["filled_buy_quantity"],
            "executed_sell_quantity": fill_summary["filled_sell_quantity"],
            "execution_net_cash_flow_krw": (
                fill_summary["gross_sell_executed_funds_krw"]
                - fill_summary["gross_buy_executed_funds_krw"]
                - fill_summary["total_fee_krw"]
            ),
            "canary_realized_pnl_calculated": False,
            "execution_net_cash_flow_is_profit": False,
        }
        return (
            {
                "activation_provenance": self._activation_payload(activation),
                "safety_binding_provenance": self._binding_payload(binding),
                "promotion_provenance": self._promotion_payload(approval),
                "termination_provenance": self._termination_payload(termination),
                "runs": run_rows,
                "run_summary": self._run_summary(run_rows),
                "selection_evidence": selections,
                "recommendation_evidence": recommendations,
                "approval_evidence": approvals,
                "order_evidence": orders,
                "fill_evidence": {"fills": fills, "summary": fill_summary},
                "operational_evidence": operational,
                "recommendation_outcome_evidence": self._outcome_evidence(
                    recommendation_rows, evidence_as_of, ceilings
                ),
                "direct_canary_financial_facts": financial,
                "portfolio_context": self._portfolio_evidence(
                    activation, runs, evidence_as_of, ceilings
                ),
                "account_bot_pnl_context": self._bot_pnl_context(
                    activation, evidence_as_of
                ),
                "safety_flags": self._safety_flags(),
            },
            findings,
            checks,
        )

    def _run_evidence(self, activation, runs):
        rows = []
        analyses = []
        findings = []
        for run in runs:
            try:
                validated = load_and_validate_live_policy_canary_run(
                    self.session, run.analysis_run_id, self.settings
                )
                if validated is None or validated[0].id != run.id:
                    raise ValueError("Canary Run validation returned another row")
            except Exception as error:
                findings.append(f"CANARY_RUN_INVALID:{run.id}:{type(error).__name__}")
            analysis = self.session.get(AnalysisRun, run.analysis_run_id)
            if (
                analysis is None
                or analysis.user_id != activation.user_id
                or analysis.pipeline_run_id != run.pipeline_run_id
                or analysis.run_type != "MARKET_UNIVERSE"
            ):
                findings.append(f"RUN_ANALYSIS_IDENTITY_INVALID:{run.id}")
            else:
                analyses.append(analysis)
            rows.append(
                {
                    "canary_run_id": run.id,
                    "run_ordinal": run.run_ordinal,
                    "analysis_run_id": run.analysis_run_id,
                    "pipeline_run_id": run.pipeline_run_id,
                    "reserved_at": run.reserved_at,
                    "run_signature": run.run_signature,
                    "baseline_policy_signature": run.baseline_policy_signature,
                    "canary_policy_signature": run.canary_policy_signature,
                    "market_universe_analysis_status": getattr(
                        analysis, "status", "MISSING"
                    ),
                    "analysis_created_at": getattr(analysis, "created_at", None),
                    "analysis_finished_at": getattr(analysis, "finished_at", None),
                }
            )
        return rows, tuple(analyses), findings

    def _selection_evidence(self, activation, analyses):
        analysis_ids = tuple(row.id for row in analyses)
        candidates = (
            tuple(
                self.session.scalars(
                    select(MarketUniverseCandidate)
                    .where(MarketUniverseCandidate.analysis_run_id.in_(analysis_ids))
                    .order_by(
                        MarketUniverseCandidate.analysis_run_id,
                        MarketUniverseCandidate.rank.asc().nullslast(),
                        MarketUniverseCandidate.id,
                    )
                )
            )
            if analysis_ids
            else ()
        )
        findings = []
        ranked = []
        held = []
        seen_ranks: set[tuple[int, int]] = set()
        for row in candidates:
            if row.user_id != activation.user_id or row.exchange != activation.exchange:
                findings.append(f"SELECTION_IDENTITY_INVALID:{row.id}")
            item = {
                "candidate_id": row.id,
                "analysis_run_id": row.analysis_run_id,
                "market": row.market,
                "rank": row.rank,
                "score": row.score,
                "selection_source": row.selection_source,
                "buy_eligible": row.buy_eligible,
                "sell_eligible": row.sell_eligible,
            }
            if row.selection_source == "HELD":
                held.append(item)
            else:
                ranked.append(item)
                if row.rank is not None:
                    key = (row.analysis_run_id, row.rank)
                    if key in seen_ranks:
                        findings.append(
                            f"DUPLICATE_RANK:{row.analysis_run_id}:{row.rank}"
                        )
                    seen_ranks.add(key)
        return (
            {
                "ranked_candidates": ranked,
                "held_only_candidates": held,
                "source": "PERSISTED_MARKET_UNIVERSE_CANDIDATES",
                "offline_replay_used": False,
            },
            candidates,
            findings,
        )

    def _recommendation_evidence(
        self, activation, candidates, evidence_as_of, ceilings
    ):
        candidate_ids = tuple(row.id for row in candidates)
        rows = (
            tuple(
                self.session.scalars(
                    select(TradeRecommendation)
                    .where(
                        TradeRecommendation.universe_candidate_id.in_(candidate_ids),
                        TradeRecommendation.id
                        <= self._ceiling(ceilings, "recommendation_id_ceiling"),
                        TradeRecommendation.created_at <= evidence_as_of,
                    )
                    .order_by(
                        TradeRecommendation.created_at,
                        TradeRecommendation.id,
                    )
                )
            )
            if candidate_ids
            else ()
        )
        evidence = []
        related_rows = []
        findings = []
        resolver = CanaryTradeProvenanceService(self.session, settings=self.settings)
        for row in rows:
            provenance = resolver.resolve(row)
            if provenance.mode == BASELINE_RECOMMENDATION:
                continue
            analysis = self.session.get(AnalysisRun, row.analysis_run_id)
            related_rows.append(row)
            item = {
                "recommendation_id": row.id,
                "analysis_run_id": row.analysis_run_id,
                "pipeline_run_id": getattr(analysis, "pipeline_run_id", None),
                "universe_candidate_id": row.universe_candidate_id,
                "market": row.market,
                "action": row.action,
                "trade_ratio": row.trade_ratio,
                "recommended_amount_krw": row.recommended_amount_krw,
                "recommended_quantity": row.recommended_quantity,
                "confidence": row.confidence,
                "status": row.status,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "provenance_mode": provenance.mode,
                "provenance_valid": provenance.valid,
            }
            evidence.append(item)
            if (
                provenance.mode != CANARY_RECOMMENDATION
                or not provenance.valid
                or provenance.activation is None
                or provenance.activation.id != activation.id
            ):
                findings.append(f"RECOMMENDATION_LINEAGE_INVALID:{row.id}")
                continue
        counts = {name: 0 for name in ("BUY", "SELL", "HOLD")}
        for row in related_rows:
            if row.action in counts:
                counts[row.action] += 1
        return (
            {
                "recommendations": evidence,
                "summary": {
                    "recommendation_count": len(evidence),
                    "buy_recommendation_count": counts["BUY"],
                    "sell_recommendation_count": counts["SELL"],
                    "hold_recommendation_count": counts["HOLD"],
                    "total_recommended_buy_amount_krw": sum(
                        (
                            Decimal(row.recommended_amount_krw)
                            for row in related_rows
                            if row.action == "BUY"
                            and row.recommended_amount_krw is not None
                        ),
                        Decimal("0"),
                    ),
                },
            },
            tuple(related_rows),
            findings,
        )

    def _approval_evidence(self, recommendation_ids, evidence_as_of, ceilings):
        rows = (
            tuple(
                self.session.scalars(
                    select(ApprovalRequest)
                    .where(
                        ApprovalRequest.recommendation_id.in_(recommendation_ids),
                        ApprovalRequest.id
                        <= self._ceiling(ceilings, "approval_request_id_ceiling"),
                        ApprovalRequest.created_at <= evidence_as_of,
                    )
                    .order_by(ApprovalRequest.created_at, ApprovalRequest.id)
                )
            )
            if recommendation_ids
            else ()
        )
        statuses = {
            name: sum(row.status == name for row in rows)
            for name in (
                "APPROVED",
                "REJECTED",
                "EXPIRED",
                "SUPERSEDED",
                "PENDING",
            )
        }
        return {
            "approval_requests": [
                {
                    "approval_request_id": row.id,
                    "recommendation_id": row.recommendation_id,
                    "status": row.status,
                    "expires_at": row.expires_at,
                    "approved_at": row.approved_at,
                    "rejected_at": row.rejected_at,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                for row in rows
            ],
            "summary": {
                "approval_requested_count": len(rows),
                "approved_count": statuses["APPROVED"],
                "rejected_count": statuses["REJECTED"],
                "expired_count": statuses["EXPIRED"],
                "superseded_count": statuses["SUPERSEDED"],
                "pending_count": statuses["PENDING"],
            },
        }

    def _order_evidence(
        self, activation, recommendation_rows, evidence_as_of, ceilings
    ):
        by_id = {row.id: row for row in recommendation_rows}
        rows = (
            tuple(
                self.session.scalars(
                    select(OrderLog)
                    .where(
                        OrderLog.recommendation_id.in_(tuple(by_id)),
                        OrderLog.trading_mode == "LIVE",
                        OrderLog.id <= self._ceiling(ceilings, "order_log_id_ceiling"),
                        OrderLog.created_at <= evidence_as_of,
                    )
                    .order_by(OrderLog.created_at, OrderLog.id)
                )
            )
            if by_id
            else ()
        )
        findings = []
        evidence = []
        for row in rows:
            recommendation = by_id[row.recommendation_id]
            provenance = CanaryTradeProvenanceService(
                self.session, settings=self.settings
            ).resolve(recommendation)
            if (
                row.user_id != recommendation.user_id
                or row.user_id != activation.user_id
                or row.exchange != recommendation.exchange
                or row.market != recommendation.market
                or row.side != recommendation.action
            ):
                findings.append(f"ORDER_LINEAGE_INVALID:{row.id}")
            audit = row.raw_response if isinstance(row.raw_response, dict) else {}
            canary = audit.get("canary")
            preflight = audit.get("preflight")
            preflight_failed = (
                isinstance(preflight, dict)
                and preflight.get("result") == "FAILED"
                and audit.get("actual_order_executed") is False
            )
            expected = {
                "mode": CANARY_RECOMMENDATION,
                "canary_activation_id": activation.id,
                "promotion_approval_id": activation.promotion_approval_id,
                "activation_signature": activation.activation_signature,
                "canary_run_id": getattr(provenance.canary_run, "id", None),
                "safety_binding_signature": getattr(
                    provenance.safety_binding, "binding_signature", None
                ),
                "max_buy_order_amount_krw": (
                    str(provenance.per_order_buy_cap)
                    if provenance.per_order_buy_cap is not None
                    else None
                ),
                "daily_max_buy_amount_krw": (
                    str(provenance.daily_buy_cap)
                    if provenance.daily_buy_cap is not None
                    else None
                ),
            }
            if (not preflight_failed and not isinstance(canary, dict)) or (
                isinstance(canary, dict)
                and any(canary.get(name) != value for name, value in expected.items())
            ):
                findings.append(f"ORDER_CANARY_AUDIT_INVALID:{row.id}")
            evidence.append(
                {
                    "order_log_id": row.id,
                    "recommendation_id": row.recommendation_id,
                    "approval_request_id": row.approval_request_id,
                    "market": row.market,
                    "side": row.side,
                    "amount_krw": row.amount_krw,
                    "quantity": row.quantity,
                    "status": row.status,
                    "exchange_order_id": row.exchange_order_id,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    "executed_quantity": row.executed_quantity,
                    "executed_funds_krw": row.executed_funds_krw,
                    "fee": row.paid_fee,
                    "remote_recovered": bool(audit.get("recovered_by_identifier")),
                    "ambiguous_create_recovery": bool(
                        audit.get("safe_error") and audit.get("recovered_by_identifier")
                    ),
                    "canary_audit_available": isinstance(canary, dict),
                    "preflight_failed_before_canary_audit": preflight_failed,
                }
            )
        counts = {
            status: sum(row.status == status for row in rows)
            for status in _ORDER_STATUSES
        }
        return (
            {
                "orders": evidence,
                "summary": {
                    "live_order_count": len(rows),
                    **{
                        f"{status.lower()}_count": count
                        for status, count in counts.items()
                    },
                    "remote_recovered_order_count": sum(
                        item["remote_recovered"] for item in evidence
                    ),
                    "ambiguous_create_recovery_count": sum(
                        item["ambiguous_create_recovery"] for item in evidence
                    ),
                    "pending_order_count": counts["LIVE_PLACED"] + counts["LIVE_WAIT"],
                    "live_pending_count": counts["LIVE_PLACED"] + counts["LIVE_WAIT"],
                    "unknown_order_count": counts["LIVE_UNKNOWN"],
                },
                "canary_attribution_source": "RELATIONAL_LINEAGE",
                "raw_audit_source_of_truth": False,
            },
            rows,
            findings,
        )

    def _fill_evidence(self, order_rows, evidence_as_of, ceilings):
        by_id = {row.id: row for row in order_rows}
        rows = (
            tuple(
                self.session.scalars(
                    select(OrderFill)
                    .where(
                        OrderFill.order_log_id.in_(tuple(by_id)),
                        OrderFill.id
                        <= self._ceiling(ceilings, "order_fill_id_ceiling"),
                        OrderFill.created_at <= evidence_as_of,
                    )
                    .order_by(OrderFill.created_at, OrderFill.id)
                )
            )
            if by_id
            else ()
        )
        evidence = [
            {
                "order_fill_id": row.id,
                "order_log_id": row.order_log_id,
                "exchange_trade_id": row.exchange_trade_id,
                "quantity": row.volume,
                "price": row.price,
                "funds": row.funds_krw,
                "side": row.side,
                "fee": None,
                "fill_timestamp": row.created_at,
            }
            for row in rows
        ]
        by_order: dict[int, list[OrderFill]] = {}
        for row in rows:
            by_order.setdefault(row.order_log_id, []).append(row)
        findings = []
        for order in order_rows:
            if order.status not in _TERMINAL_EXECUTION_STATUSES:
                continue
            order_fills = by_order.get(order.id, [])
            quantity = sum((Decimal(row.volume) for row in order_fills), Decimal("0"))
            funds = sum((Decimal(row.funds_krw) for row in order_fills), Decimal("0"))
            if (
                order.executed_quantity is None
                or order.executed_funds_krw is None
                or Decimal(order.executed_quantity) != quantity
                or Decimal(order.executed_funds_krw) != funds
            ):
                findings.append(f"ORDER_FILL_AGGREGATE_MISMATCH:{order.id}")

        def total(side, field):
            return sum(
                (
                    Decimal(getattr(row, field))
                    for row in rows
                    if (row.side or by_id[row.order_log_id].side) == side
                ),
                Decimal("0"),
            )

        buy_fees = sum(
            (
                Decimal(row.paid_fee)
                for row in order_rows
                if row.side == "BUY" and row.paid_fee is not None
            ),
            Decimal("0"),
        )
        sell_fees = sum(
            (
                Decimal(row.paid_fee)
                for row in order_rows
                if row.side == "SELL" and row.paid_fee is not None
            ),
            Decimal("0"),
        )
        summary = {
            "fill_count": len(rows),
            "filled_buy_quantity": total("BUY", "volume"),
            "filled_sell_quantity": total("SELL", "volume"),
            "gross_buy_executed_funds_krw": total("BUY", "funds_krw"),
            "gross_sell_executed_funds_krw": total("SELL", "funds_krw"),
            "buy_fee_krw": buy_fees,
            "sell_fee_krw": sell_fees,
            "total_fee_krw": buy_fees + sell_fees,
            "fee_integrity_verifiable_from_order_fills": False,
        }
        return evidence, summary, findings

    def _operational_evidence(
        self,
        activation,
        runs,
        recommendation_ids,
        order_ids,
        evidence_as_of,
        ceilings,
    ):
        pipeline_ids = tuple(run.pipeline_run_id for run in runs)
        conditions = [
            OperationalAlert.dedup_key == f"CANARY_STARTED:{activation.id}",
            OperationalAlert.dedup_key == f"CANARY_STOPPED:{activation.id}",
        ]
        if recommendation_ids:
            conditions.append(
                OperationalAlert.recommendation_id.in_(recommendation_ids)
            )
        if order_ids:
            conditions.append(OperationalAlert.order_log_id.in_(order_ids))
        if pipeline_ids:
            conditions.append(OperationalAlert.pipeline_run_id.in_(pipeline_ids))
        rows = tuple(
            self.session.scalars(
                select(OperationalAlert)
                .where(
                    or_(*conditions),
                    OperationalAlert.user_id == activation.user_id,
                    OperationalAlert.id
                    <= self._ceiling(ceilings, "operational_alert_id_ceiling"),
                    OperationalAlert.created_at <= evidence_as_of,
                )
                .order_by(OperationalAlert.created_at, OperationalAlert.id)
            )
        )
        reason_names = {
            "PER_ORDER_LIMIT": "per_order_limit_block_count",
            "DAILY_LIMIT": "daily_limit_block_count",
            "BUDGET_LOCK_BUSY": "budget_lock_busy_count",
            "INVALID_CANARY_PROVENANCE": "invalid_provenance_alert_count",
        }
        return {
            "alerts": [
                {
                    "operational_alert_id": row.id,
                    "alert_type": row.alert_type,
                    "error_code": row.error_code,
                    "structured_reason_available": row.error_code is not None,
                    "pipeline_run_id": row.pipeline_run_id,
                    "analysis_run_id": row.analysis_run_id,
                    "order_log_id": row.order_log_id,
                    "recommendation_id": row.recommendation_id,
                    "delivery_status": row.delivery_status,
                    "resolved_at": row.resolved_at,
                    "created_at": row.created_at,
                }
                for row in rows
            ],
            "summary": {
                "canary_started_alert_count": sum(
                    row.alert_type == "LIVE_CANARY_STARTED" for row in rows
                ),
                "canary_stopped_alert_count": sum(
                    row.alert_type == "LIVE_CANARY_STOPPED" for row in rows
                ),
                **{
                    name: sum(row.error_code == code for row in rows)
                    for code, name in reason_names.items()
                },
                "pipeline_failure_alert_count": sum(
                    row.alert_type == "PIPELINE_FAILURE" for row in rows
                ),
                "stale_live_order_alert_count": sum(
                    row.alert_type == "STALE_LIVE_ORDER" for row in rows
                ),
                "alert_delivery_sent_count": sum(
                    row.delivery_status == "SENT" for row in rows
                ),
                "alert_delivery_failed_count": sum(
                    row.delivery_status == "FAILED" for row in rows
                ),
                "unresolved_alert_count": sum(row.resolved_at is None for row in rows),
                "legacy_unstructured_canary_alert_count": sum(
                    row.alert_type.startswith("LIVE_CANARY_") and row.error_code is None
                    for row in rows
                ),
            },
        }

    def _outcome_evidence(self, recommendation_rows, evidence_as_of, ceilings):
        recommendation_ids = tuple(row.id for row in recommendation_rows)
        rows = (
            tuple(
                self.session.scalars(
                    select(TradeRecommendationOutcome)
                    .where(
                        TradeRecommendationOutcome.recommendation_id.in_(
                            recommendation_ids
                        ),
                        TradeRecommendationOutcome.id
                        <= self._ceiling(ceilings, "recommendation_outcome_id_ceiling"),
                        TradeRecommendationOutcome.created_at <= evidence_as_of,
                    )
                    .order_by(
                        TradeRecommendationOutcome.recommendation_id,
                        TradeRecommendationOutcome.horizon_minutes,
                        TradeRecommendationOutcome.id,
                    )
                )
            )
            if recommendation_ids
            else ()
        )
        return {
            "outcomes": [
                {
                    "recommendation_outcome_id": row.id,
                    "recommendation_id": row.recommendation_id,
                    "horizon_minutes": row.horizon_minutes,
                    "reference_price": row.reference_price,
                    "end_price": row.end_price,
                    "market_return_percentage": row.market_return_percentage,
                    "action_aligned_return_percentage": (
                        row.action_aligned_return_percentage
                    ),
                    "directional_result": row.directional_result,
                    "evaluation_status": row.evaluation_status,
                    "target_at": row.target_at,
                    "evaluated_at": row.evaluated_at,
                }
                for row in rows
            ],
            "recommendation_outcome_count": len(rows),
            "canonical_horizons_minutes": [60, 240, 1440],
            "outcome_is_actual_execution_pnl": False,
        }

    def _portfolio_evidence(self, activation, runs, evidence_as_of, ceilings):
        rows = tuple(
            self.session.scalars(
                select(PortfolioSnapshot)
                .where(
                    PortfolioSnapshot.user_id == activation.user_id,
                    PortfolioSnapshot.exchange == activation.exchange,
                    PortfolioSnapshot.id
                    <= self._ceiling(ceilings, "portfolio_snapshot_id_ceiling"),
                    PortfolioSnapshot.captured_at <= evidence_as_of,
                )
                .order_by(PortfolioSnapshot.captured_at, PortfolioSnapshot.id)
            )
        )
        before = [
            row
            for row in rows
            if self._utc(row.captured_at, "portfolio captured_at")
            < self._utc(activation.started_at, "activation started_at")
        ]
        pipeline_ids = {run.pipeline_run_id for run in runs}
        same_pipeline = [row for row in rows if row.pipeline_run_id in pipeline_ids]
        selected = []
        for row in (
            ([before[-1]] if before else [])
            + same_pipeline
            + ([rows[-1]] if rows else [])
        ):
            if row.id not in {existing.id for existing in selected}:
                selected.append(row)
        first_value = before[-1].total_value_krw if before else None
        last_value = rows[-1].total_value_krw if rows else None
        delta = (
            Decimal(last_value) - Decimal(first_value)
            if first_value is not None and last_value is not None
            else None
        )
        return {
            "snapshots": [self._portfolio_payload(row) for row in selected],
            "latest_before_activation_snapshot_id": (before[-1].id if before else None),
            "same_pipeline_snapshot_ids": [row.id for row in same_pipeline],
            "latest_snapshot_id": rows[-1].id if rows else None,
            "account_portfolio_value_delta_krw": delta,
            "portfolio_delta_is_canary_pnl": False,
        }

    def _bot_pnl_context(self, activation, evidence_as_of):
        row = self.session.scalar(
            select(BotTradingPnlSummary)
            .where(
                BotTradingPnlSummary.user_id == activation.user_id,
                BotTradingPnlSummary.exchange == activation.exchange,
                BotTradingPnlSummary.calculated_at <= evidence_as_of,
            )
            .order_by(
                BotTradingPnlSummary.calculated_at.desc(),
                BotTradingPnlSummary.id.desc(),
            )
            .limit(1)
        )
        if row is None:
            return {"available": False, "canary_attributed": False}
        return {
            "available": True,
            "canary_attributed": False,
            "accounting_status": row.accounting_status,
            "source_signature": row.source_signature,
            "source_order_count": row.source_order_count,
            "recognized_realized_pnl_krw": row.recognized_realized_pnl_krw,
            "open_bot_cost_basis_krw": row.open_bot_cost_basis_krw,
            "calculated_at": row.calculated_at,
        }

    @staticmethod
    def _activation_payload(row):
        return {
            "canary_activation_id": row.id,
            "activation_signature": row.activation_signature,
            "candidate_id": row.candidate_id,
            "user_id": row.user_id,
            "exchange": row.exchange,
            "quote_asset": row.quote_asset,
            "scenario_name": row.scenario_name,
            "scenario_definition_signature": (row.scenario_definition_signature),
            "baseline_policy_signature": row.baseline_policy_signature,
            "canary_policy_signature": row.canary_policy_signature,
            "effective_top_n": row.effective_top_n,
            "started_at": row.started_at,
            "expires_at": row.expires_at,
            "max_analysis_runs": row.max_analysis_runs,
        }

    @staticmethod
    def _binding_payload(row):
        return {
            "safety_binding_id": row.id,
            "safety_binding_signature": row.binding_signature,
            "order_safety_policy_schema_version": (
                row.order_safety_policy_schema_version
            ),
            "order_safety_policy_signature": (row.order_safety_policy_signature),
            "max_buy_order_amount_krw": row.max_buy_order_amount_krw,
            "daily_max_buy_amount_krw": row.daily_max_buy_amount_krw,
        }

    @staticmethod
    def _promotion_payload(row):
        return {
            "promotion_approval_id": row.id,
            "promotion_approval_signature": row.approval_signature,
            "shadow_enrollment_id": row.shadow_enrollment_id,
            "review_decision_signature": row.review_decision_signature,
        }

    @staticmethod
    def _termination_payload(row):
        if row is None:
            return None
        return {
            "termination_event_id": row.id,
            "termination_signature": row.termination_signature,
            "termination_source": row.termination_source,
            "termination_reason": row.termination_reason,
            "terminated_at": row.terminated_at,
        }

    @staticmethod
    def _portfolio_payload(row):
        return {
            "portfolio_snapshot_id": row.id,
            "pipeline_run_id": row.pipeline_run_id,
            "cash_total_krw": row.cash_total_krw,
            "priced_positions_value_krw": row.priced_positions_value_krw,
            "known_total_value_krw": row.known_total_value_krw,
            "total_value_krw": row.total_value_krw,
            "unrealized_pnl_krw": row.unrealized_pnl_krw,
            "valuation_status": row.valuation_status,
            "valuation_policy_signature": row.valuation_policy_signature,
            "captured_at": row.captured_at,
        }

    @staticmethod
    def _run_summary(rows):
        statuses = [row["market_universe_analysis_status"] for row in rows]
        return {
            "reserved_run_count": len(rows),
            "successful_market_universe_run_count": statuses.count("SUCCESS"),
            "failed_market_universe_run_count": statuses.count("FAILED"),
            "unfinished_market_universe_run_count": sum(
                status not in {"SUCCESS", "FAILED"} for status in statuses
            ),
            "successful_run_count": statuses.count("SUCCESS"),
            "failed_run_count": statuses.count("FAILED"),
        }

    @staticmethod
    def _safety_flags():
        return {
            "database_write": False,
            "external_calls": False,
            "live_policy_change": False,
            "live_order_change": False,
            "ranking_runtime_changed": False,
            "canary_state_changed": False,
            "promotion_performed": False,
            "full_live_promotion_performed": False,
            "sample_sufficiency_assessed": False,
            "policy_decision_performed": False,
            "statistical_inference_performed": False,
        }

    @staticmethod
    def _integrity(
        *,
        findings,
        activation=True,
        binding=True,
        promotion=True,
        termination=True,
        runs=True,
        recommendations=True,
        orders=True,
        fills=True,
        snapshot=True,
    ):
        return {
            "activation_provenance_verified": activation,
            "safety_binding_verified": binding,
            "promotion_provenance_verified": promotion,
            "termination_provenance_verified": termination,
            "all_canary_runs_verified": runs,
            "all_recommendation_lineages_verified": recommendations,
            "all_canary_order_lineages_verified": orders,
            "order_fill_integrity_verified": fills,
            "snapshot_consistency_verified": snapshot,
            "findings": list(findings),
        }

    def _empty_payload(self):
        zero = Decimal("0")
        return {
            "activation_provenance": None,
            "safety_binding_provenance": None,
            "promotion_provenance": None,
            "termination_provenance": None,
            "runs": [],
            "run_summary": {
                "reserved_run_count": 0,
                "successful_market_universe_run_count": 0,
                "failed_market_universe_run_count": 0,
                "unfinished_market_universe_run_count": 0,
                "successful_run_count": 0,
                "failed_run_count": 0,
            },
            "selection_evidence": {
                "ranked_candidates": [],
                "held_only_candidates": [],
                "source": "PERSISTED_MARKET_UNIVERSE_CANDIDATES",
                "offline_replay_used": False,
            },
            "recommendation_evidence": {
                "recommendations": [],
                "summary": {
                    "recommendation_count": 0,
                    "buy_recommendation_count": 0,
                    "sell_recommendation_count": 0,
                    "hold_recommendation_count": 0,
                    "total_recommended_buy_amount_krw": zero,
                },
            },
            "approval_evidence": {
                "approval_requests": [],
                "summary": {
                    "approval_requested_count": 0,
                    "approved_count": 0,
                    "rejected_count": 0,
                    "expired_count": 0,
                    "superseded_count": 0,
                    "pending_count": 0,
                },
            },
            "order_evidence": {
                "orders": [],
                "summary": {
                    "live_order_count": 0,
                    **{f"{status.lower()}_count": 0 for status in _ORDER_STATUSES},
                    "remote_recovered_order_count": 0,
                    "ambiguous_create_recovery_count": 0,
                    "pending_order_count": 0,
                    "live_pending_count": 0,
                    "unknown_order_count": 0,
                },
                "canary_attribution_source": "RELATIONAL_LINEAGE",
                "raw_audit_source_of_truth": False,
            },
            "fill_evidence": {
                "fills": [],
                "summary": {
                    "fill_count": 0,
                    "filled_buy_quantity": zero,
                    "filled_sell_quantity": zero,
                    "gross_buy_executed_funds_krw": zero,
                    "gross_sell_executed_funds_krw": zero,
                    "buy_fee_krw": zero,
                    "sell_fee_krw": zero,
                    "total_fee_krw": zero,
                    "fee_integrity_verifiable_from_order_fills": False,
                },
            },
            "operational_evidence": {
                "alerts": [],
                "summary": {
                    "canary_started_alert_count": 0,
                    "canary_stopped_alert_count": 0,
                    "per_order_limit_block_count": 0,
                    "daily_limit_block_count": 0,
                    "budget_lock_busy_count": 0,
                    "invalid_provenance_alert_count": 0,
                    "pipeline_failure_alert_count": 0,
                    "stale_live_order_alert_count": 0,
                    "alert_delivery_sent_count": 0,
                    "alert_delivery_failed_count": 0,
                    "unresolved_alert_count": 0,
                    "legacy_unstructured_canary_alert_count": 0,
                },
            },
            "recommendation_outcome_evidence": {
                "outcomes": [],
                "recommendation_outcome_count": 0,
                "canonical_horizons_minutes": [60, 240, 1440],
                "outcome_is_actual_execution_pnl": False,
            },
            "direct_canary_financial_facts": {
                "total_recommended_buy_amount_krw": zero,
                "submitted_buy_amount_krw": zero,
                "gross_buy_executed_funds_krw": zero,
                "gross_sell_executed_funds_krw": zero,
                "total_fee_krw": zero,
                "executed_buy_quantity": zero,
                "executed_sell_quantity": zero,
                "execution_net_cash_flow_krw": zero,
                "canary_realized_pnl_calculated": False,
                "execution_net_cash_flow_is_profit": False,
            },
            "portfolio_context": {
                "snapshots": [],
                "portfolio_delta_is_canary_pnl": False,
            },
            "account_bot_pnl_context": {
                "available": False,
                "canary_attributed": False,
            },
            "integrity": self._integrity(findings=()),
            "safety_flags": self._safety_flags(),
        }

    def _finish(
        self,
        *,
        status,
        lifecycle,
        activation_id,
        candidate_id,
        evidence_as_of,
        consistency,
        ceilings,
        payload,
    ):
        unsigned = {
            "report_type": REPORT_TYPE,
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "canary_activation_id": activation_id,
            "candidate_id": candidate_id,
            "status": status,
            "lifecycle_state": lifecycle,
            "evidence_as_of": evidence_as_of,
            "snapshot_consistency": consistency,
            **ceilings,
            **payload,
        }
        return LiveCanaryEvidenceReport(
            status=status,
            lifecycle_state=lifecycle,
            canary_activation_id=activation_id,
            candidate_id=candidate_id,
            evidence_as_of=evidence_as_of,
            snapshot_consistency=consistency,
            ceilings=ceilings,
            payload=payload,
            evidence_signature=live_canary_evidence_signature(unsigned),
        )

    def _lifecycle(self, activation, termination, run_count, evidence_as_of):
        if termination is not None:
            return STOPPED
        if evidence_as_of >= self._utc(activation.expires_at, "activation expires_at"):
            return EXPIRED
        if run_count >= activation.max_analysis_runs:
            return EXHAUSTED
        return ACTIVE

    @staticmethod
    def _order_was_submitted(row):
        audit = row.raw_response if isinstance(row.raw_response, dict) else {}
        preflight = audit.get("preflight")
        return not (
            isinstance(preflight, dict)
            and preflight.get("result") == "FAILED"
            and audit.get("actual_order_executed") is False
        )

    @staticmethod
    def _ceiling(ceilings, name):
        return ceilings.get(name) or 0

    @staticmethod
    def _utc(value, field):
        if not isinstance(value, datetime):
            raise LiveCanaryEvidenceError(f"{field} must be a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise LiveCanaryEvidenceError(f"{field} must be timezone-aware")
        return value.astimezone(UTC)
