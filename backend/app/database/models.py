"""
SQLAlchemy declarative models — DATABASE FOUNDATION v2

Tables
------
webhook_events     Every Razorpay webhook received (idempotency + audit)
recovery_cases     Central recovery opportunity object (full lifecycle)
recovery_attempts  One row per attempted recovery action (immutable history)

State machines are defined in app.recovery.states — imported here for reference
only; the columns use plain String so the DB has no enum dependency.
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── WebhookEvent ──────────────────────────────────────────────────────────────

class WebhookEvent(Base):
    """
    Every Razorpay webhook received.
    razorpay_event_id is the idempotency key (e.g. evt_ABC123).
    """
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    razorpay_event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    event_type: Mapped[str]        = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict]          = mapped_column(JSONB,      nullable=False)
    received_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # RECEIVED → QUEUED → PROCESSED | DUPLICATE | FAILED
    status: Mapped[str]                      = mapped_column(String(16), nullable=False, server_default="RECEIVED", index=True)
    processed_at: Mapped[datetime | None]    = mapped_column(DateTime(timezone=True), nullable=True)

    recovery_cases: Mapped[list["RecoveryCase"]] = relationship(back_populates="source_event")

    def __repr__(self) -> str:
        return f"<WebhookEvent id={self.id} type={self.event_type} status={self.status}>"


# ── RecoveryCase ──────────────────────────────────────────────────────────────

class RecoveryCase(Base):
    """
    Central object for a revenue recovery opportunity.

    Lifecycle (see app.recovery.states for full transition table):
      CREATED → DIAGNOSED → SCORED → ACTION_RECOMMENDED
              → POLICY_APPROVED → SCHEDULED → EXECUTING
              → EXECUTED → RECOVERED | RE_EVALUATE → SCORED …
              → STOPPED | ESCALATED

    Economic model fields are written by the LangGraph orchestrator.
    Subscription / mandate fields are written by the worker on intake.
    """
    __tablename__ = "recovery_cases"

    id: Mapped[int]       = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    case_ref: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)

    # PAYMENT_FAILURE | CHECKOUT_ABANDONED | SUBSCRIPTION_FAILURE | OVERDUE_RECEIVABLE
    case_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # ── Razorpay identifiers ──────────────────────────────────────────────────
    payment_id: Mapped[str | None]      = mapped_column(String(64), nullable=True, index=True)
    order_id: Mapped[str | None]        = mapped_column(String(64), nullable=True, index=True)
    customer_id: Mapped[str | None]     = mapped_column(String(64), nullable=True)
    subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    invoice_id: Mapped[str | None]      = mapped_column(String(64), nullable=True)

    # ── Financials ────────────────────────────────────────────────────────────
    amount: Mapped[int | None]  = mapped_column(BigInteger, nullable=True)   # paise
    currency: Mapped[str]       = mapped_column(String(8),  nullable=False,  server_default="INR")

    # ── Lifecycle status ──────────────────────────────────────────────────────
    status: Mapped[str]                    = mapped_column(String(24), nullable=False, server_default="CREATED", index=True)
    attempt_count: Mapped[int]             = mapped_column(Integer,    nullable=False, server_default="0")
    last_action: Mapped[str | None]        = mapped_column(String(32), nullable=True)   # last attempted action
    recommended_action: Mapped[str | None] = mapped_column(String(32), nullable=True)   # orchestrator recommendation

    # ── Failure context ───────────────────────────────────────────────────────
    failure_reason: Mapped[str | None]      = mapped_column(Text, nullable=True)   # e.g. BAD_REQUEST_ERROR
    payment_method: Mapped[str | None]      = mapped_column(String(32), nullable=True)  # card | upi | netbanking
    failure_category: Mapped[str | None]    = mapped_column(String(32), nullable=True)  # ISSUER_DOWN | INSUFFICIENT_FUNDS …

    # ── Economic model (written by orchestrator) ──────────────────────────────
    p0: Mapped[float | None]                  = mapped_column(Numeric(6, 4), nullable=True)  # P(recovery | no action)
    recommended_action_pa: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)  # P(recovery | recommended action)
    expected_uplift: Mapped[float | None]     = mapped_column(Numeric(6, 4), nullable=True)  # pa - p0
    expected_net_recovery: Mapped[int | None] = mapped_column(BigInteger,    nullable=True)  # paise

    # ── Stopping / escalation ─────────────────────────────────────────────────
    stop_reason: Mapped[str | None]       = mapped_column(Text, nullable=True)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Scheduling ────────────────────────────────────────────────────────────
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Subscription / mandate context (populated for SUBSCRIPTION_FAILURE) ───
    mandate_id: Mapped[str | None]               = mapped_column(String(64), nullable=True)
    mandate_status: Mapped[str | None]           = mapped_column(String(32), nullable=True)
    notification_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_allowed_debit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_budget_remaining: Mapped[int | None]   = mapped_column(Integer, nullable=True)

    # ── Source webhook ────────────────────────────────────────────────────────
    source_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("webhook_events.id"), nullable=True, index=True
    )
    source_event: Mapped["WebhookEvent | None"] = relationship(back_populates="recovery_cases")

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_at: Mapped[datetime]          = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime]          = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_at: Mapped[datetime | None]  = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    attempts: Mapped[list["RecoveryAttempt"]] = relationship(
        back_populates="case", order_by="RecoveryAttempt.attempted_at"
    )

    def __repr__(self) -> str:
        return (
            f"<RecoveryCase ref={self.case_ref} type={self.case_type} "
            f"status={self.status} attempts={self.attempt_count}>"
        )


# ── RecoveryAttempt ───────────────────────────────────────────────────────────

class RecoveryAttempt(Base):
    """
    Immutable record of one recovery action taken on a case.

    One case → many attempts (1:N).
    Never update a row here — append only.

    Example lifecycle for one case:
      attempt 1  RETRY_PAYMENT      FAILED    p=0.65
      attempt 2  SEND_PAYMENT_LINK  SENT      p=0.72
      attempt 3  SEND_PAYMENT_LINK  PAID      p=0.72  ← case → RECOVERED
    """
    __tablename__ = "recovery_attempts"

    id: Mapped[int]      = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("recovery_cases.id"), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)   # 1-based per case

    # ── Action ────────────────────────────────────────────────────────────────
    # RETRY_PAYMENT | SEND_PAYMENT_LINK | OFFER_DISCOUNT |
    # WAIT_AND_RETRY | ESCALATE | NO_ACTION
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # ── Economic snapshot at decision time ────────────────────────────────────
    p0_at_decision: Mapped[float | None]              = mapped_column(Numeric(6, 4), nullable=True)
    pa_at_decision: Mapped[float | None]              = mapped_column(Numeric(6, 4), nullable=True)
    expected_net_recovery_at_decision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    intervention_cost: Mapped[int | None]             = mapped_column(Integer,        nullable=True)  # paise

    # ── Policy ────────────────────────────────────────────────────────────────
    # APPROVED | BLOCKED
    policy_decision: Mapped[str]             = mapped_column(String(16), nullable=False)
    policy_reason: Mapped[str | None]        = mapped_column(Text,       nullable=True)

    # ── Execution ─────────────────────────────────────────────────────────────
    # PENDING | SENT | EXECUTED | FAILED | CANCELLED | SKIPPED
    execution_status: Mapped[str]            = mapped_column(String(16), nullable=False, server_default="PENDING")
    razorpay_reference: Mapped[str | None]   = mapped_column(String(64), nullable=True)  # order/payment link ID returned
    execution_error: Mapped[str | None]      = mapped_column(Text,       nullable=True)

    # ── Outcome (filled after webhook confirms result) ────────────────────────
    # PAID | FAILED | EXPIRED | PENDING | UNKNOWN
    outcome: Mapped[str | None]              = mapped_column(String(16), nullable=True, index=True)
    outcome_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Freshness check (pre-execution re-authorisation) ─────────────────────
    freshness_checked_at: Mapped[datetime | None]  = mapped_column(DateTime(timezone=True), nullable=True)
    freshness_passed: Mapped[bool | None]          = mapped_column(nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    attempted_at: Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    scheduled_for: Mapped[datetime | None]   = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None]     = mapped_column(DateTime(timezone=True), nullable=True)

    # ── LLM reasoning trace ───────────────────────────────────────────────────
    # stores the orchestrator's diagnosis + reasoning for this attempt
    orchestrator_trace: Mapped[dict | None]  = mapped_column(JSONB, nullable=True)

    # ── Relationship ──────────────────────────────────────────────────────────
    case: Mapped["RecoveryCase"] = relationship(back_populates="attempts")

    def __repr__(self) -> str:
        return (
            f"<RecoveryAttempt case_id={self.case_id} #{self.attempt_number} "
            f"action={self.action} policy={self.policy_decision} outcome={self.outcome}>"
        )


# ── ScheduledAction ───────────────────────────────────────────────────────────

class ScheduledAction(Base):
    """
    A future recovery action scheduled by the orchestrator.
    Persisted in PostgreSQL so restarts never lose pending work.

    The scheduler polls this table every 30s, freshness-checks each due row,
    and either executes it or cancels it (stale action demo).
    """
    __tablename__ = "scheduled_actions"

    id: Mapped[int]      = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("recovery_cases.id"), nullable=False, index=True)

    # RETRY_PAYMENT | WAIT_AND_RETRY | SEND_PAYMENT_LINK
    action_type: Mapped[str]    = mapped_column(String(32), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    # PENDING → EXECUTING → EXECUTED | CANCELLED
    status: Mapped[str]         = mapped_column(String(16), nullable=False, server_default="PENDING", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime]          = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    executed_at: Mapped[datetime | None]  = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<ScheduledAction case_id={self.case_id} type={self.action_type} due={self.scheduled_for} status={self.status}>"
