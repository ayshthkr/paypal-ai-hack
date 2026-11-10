from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Boolean, Index, Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    provider_customer_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="demo", index=True)
    drift: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invoices: Mapped[list["Invoice"]] = relationship(back_populates="client")


class DemoState(Base):
    __tablename__ = "demo_state"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="demo")
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    seeded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    reset_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (Index("ix_invoices_status_due_date", "status", "due_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    paypal_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    paid: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    outstanding: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    disputed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    promised_payment_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="demo", index=True)
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    viewed_by_recipient: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    viewed_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    partial_payment_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payment_term_type: Mapped[str | None] = mapped_column(String(32))
    reminder_events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reminder_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    material_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    client: Mapped[Client] = relationship(back_populates="invoices")
    payments: Mapped[list["Payment"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    predictions: Mapped[list["Prediction"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_invoice_occurred", "invoice_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    provider_payment_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    invoice: Mapped[Invoice] = relationship(back_populates="payments")


class InvoiceEvent(Base):
    __tablename__ = "invoice_events"
    __table_args__ = (Index("ix_invoice_events_invoice_occurred", "invoice_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    invoice_id: Mapped[str | None] = mapped_column(String(36))
    provider_event_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processing_status: Mapped[str] = mapped_column(String(24), nullable=False, default="received")
    failure_reason: Mapped[str | None] = mapped_column(Text)


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("invoice_id", "scoring_date", "fingerprint", "model_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    scoring_date: Mapped[date] = mapped_column(Date, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    model_label: Mapped[str] = mapped_column(String(64), nullable=False, default="kaplan_meier")
    history_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    curve: Mapped[list] = mapped_column(JSON, nullable=False)
    quantiles: Mapped[dict] = mapped_column(JSON, nullable=False)
    probabilities: Mapped[dict] = mapped_column(JSON, nullable=False)
    reasons: Mapped[list] = mapped_column(JSON, nullable=False)
    invoice: Mapped[Invoice] = relationship(back_populates="predictions")


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (Index("uq_proposed_recommendation_version", "invoice_id", "invoice_version", unique=True,
                           postgresql_where=text("state = 'proposed'"), sqlite_where=text("state = 'proposed'")),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    tone: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    draft_text: Mapped[str] = mapped_column(Text, nullable=False)
    draft_source: Mapped[str] = mapped_column(String(24), nullable=False, default="template")
    drafted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    edited_text: Mapped[str] = mapped_column(Text, nullable=False)
    invoice_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_step: Mapped[int | None] = mapped_column(Integer)
    policy_date: Mapped[date | None] = mapped_column(Date, index=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed", index=True)
    approved_hash: Mapped[str | None] = mapped_column(String(64))
    approved_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invoice: Mapped[Invoice] = relationship(back_populates="recommendations")
    attempts: Mapped[list["ActionAttempt"]] = relationship(back_populates="recommendation", cascade="all, delete-orphan")


class ActionAttempt(Base):
    __tablename__ = "action_attempts"
    __table_args__ = (UniqueConstraint("attempt_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"), index=True)
    attempt_key: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="sending")
    provider_reference: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped[Recommendation] = relationship(back_populates="attempts")


class PolicyDecisionLog(Base):
    __tablename__ = "policy_decision_logs"
    __table_args__ = (UniqueConstraint("fingerprint"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    decision_date: Mapped[date] = mapped_column(Date, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    features: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    chosen_action: Mapped[str] = mapped_column(String(64), nullable=False)
    alternatives: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    assigned_holdback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    outcome: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class ReplyTriage(Base):
    __tablename__ = "reply_triages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    reply_text: Mapped[str] = mapped_column(Text, nullable=False)
    classification: Mapped[str] = mapped_column(String(24), nullable=False)
    promised_payment_date: Mapped[date | None] = mapped_column(Date)
    classifier: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running", index=True)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_refreshes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class ReadModel(Base):
    """Durable, versioned projection; no estimators run on a warm read."""
    __tablename__ = "read_models"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AIAudit(Base):
    __tablename__ = "ai_audits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    invoice_id: Mapped[str | None] = mapped_column(String(36), index=True)
    facts: Mapped[dict] = mapped_column(JSON, nullable=False)
    output: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class OperatorSession(Base):
    __tablename__ = "operator_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    revoked_at: Mapped[int | None] = mapped_column(Integer)


class SandboxInvoiceOperation(Base):
    __tablename__ = "sandbox_invoice_operations"

    idempotency_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    paypal_invoice_id: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved", index=True)
    response: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)
