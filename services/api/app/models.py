from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
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


class Invoice(Base):
    __tablename__ = "invoices"

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

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
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
    feature_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    curve: Mapped[list] = mapped_column(JSON, nullable=False)
    quantiles: Mapped[dict] = mapped_column(JSON, nullable=False)
    probabilities: Mapped[dict] = mapped_column(JSON, nullable=False)
    reasons: Mapped[list] = mapped_column(JSON, nullable=False)
    invoice: Mapped[Invoice] = relationship(back_populates="predictions")


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    tone: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    draft_text: Mapped[str] = mapped_column(Text, nullable=False)
    draft_source: Mapped[str] = mapped_column(String(24), nullable=False, default="template")
    drafted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    edited_text: Mapped[str] = mapped_column(Text, nullable=False)
    invoice_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_step: Mapped[int | None] = mapped_column(Integer)
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
    chosen_action: Mapped[str] = mapped_column(String(32), nullable=False)
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
