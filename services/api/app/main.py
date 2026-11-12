from __future__ import annotations

import csv
import io
import hashlib
import hmac
import json
import re
import statistics
import logging
import asyncio
from collections import Counter, deque
from time import monotonic, perf_counter
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.http_cache import PrivateReadCache, cached_aggregate
from app.telemetry import metrics, timed
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.models import ActionAttempt, Client, CollectionReview, DemoSession, DemoState, Invoice, InvoiceEvent, OperatorSession, Payment, PolicyDecisionLog, Prediction, Recommendation, ReplyTriage, SandboxInvoiceOperation, SyncRun, ReadModel, AIAudit
from app.services.ai import classify_reply, extract_promised_date, generate_reminder_draft
from app.security import COOKIE_NAME, create_session, require_operator, session_claims, session_user
from app.services.dates import business_date, event_date
from app.services.drift import client_drift
from app.services.forecast import _as_record, forecast_invoice, warm_model
from app.services.paypal import PayPalAdapter, PayPalError, webhook_invoice_id
from app.services.policy import DAILY_SUGGESTION_CAP, CLOSED_STATUSES, decide, holdback_assignment, render_message
from app.services.sync import refresh_paypal_invoice, sync_paypal, upsert_paypal_invoice
from ml.activity import fit_bgnbd

settings = get_settings()
_REQUEST_DEMO_AS_OF: ContextVar[date | None] = ContextVar("request_demo_as_of", default=None)
_REQUEST_DEMO_SOURCE: ContextVar[str | None] = ContextVar("request_demo_source", default=None)
ACTIVITY_EXCLUDED_STATUSES = {"CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"}
PORTFOLIO_CONTEXT_KEY = "latepayer_portfolio_context"
_ACTIVITY_CACHE: dict[tuple, dict] = {}
_ACTIVITY_CACHE_LOCK = Lock()
_POLICY_CANDIDATE_CACHE: dict[tuple, frozenset[str]] = {}
_POLICY_CANDIDATE_CACHE_LOCK = Lock()
_AUTH_FAILURES: dict[str, deque[float]] = {}
_AUTH_LOCK = Lock()
_DEMO_SESSION_LOCK = Lock()
_MAX_AUTH_FAILURE_KEYS = 4096
@asynccontextmanager
async def lifespan(app):
    warm_model()
    yield


app = FastAPI(lifespan=lifespan, title="Late Payer", version="1.0.0", description="Invoice payment forecasts and approved PayPal reminders.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Requested-With"],
)


def _is_demo_source(source: str | None) -> bool:
    return bool(source == "demo" or (source and source.startswith("demo_")))


def _public_demo_source() -> str:
    return _REQUEST_DEMO_SOURCE.get() or "demo"


def _ensure_demo_session(session_id: str) -> str:
    canonical_id = str(UUID(session_id))
    with _DEMO_SESSION_LOCK, SessionLocal() as db:
        existing = db.get(DemoSession, canonical_id)
        if existing:
            return existing.source
        base_state = db.get(DemoState, "demo")
        if not base_state:
            raise RuntimeError("The synthetic demo snapshot is not seeded yet.")
        clients = list(db.scalars(select(Client).where(Client.source == "demo")).all())
        invoices = list(db.scalars(select(Invoice).where(Invoice.source == "demo")).all())
        payments = list(db.scalars(select(Payment).where(Payment.source == "demo")).all())
        if not clients or not invoices:
            raise RuntimeError("The synthetic demo snapshot is empty.")
        source = f"demo_{UUID(canonical_id).hex[:11]}"
        client_ids = {row.id: str(uuid4()) for row in clients}
        invoice_ids = {row.id: str(uuid4()) for row in invoices}

        def values(row, model, overrides: dict):
            output = {column.key: getattr(row, column.key) for column in model.__table__.columns}
            output.update(overrides)
            return output

        try:
            db.add(DemoSession(id=canonical_id, source=source, as_of_date=base_state.as_of_date))
            db.flush()
            db.execute(insert(Client), [
                values(row, Client, {"id": client_ids[row.id], "source": source, "provider_customer_id": None})
                for row in clients
            ])
            db.execute(insert(Invoice), [
                values(row, Invoice, {
                    "id": invoice_ids[row.id], "client_id": client_ids[row.client_id],
                    "source": source, "paypal_id": None,
                })
                for row in invoices
            ])
            payment_rows = [
                values(row, Payment, {
                    "id": str(uuid4()),
                    "provider_payment_id": f"{source}-{row.provider_payment_id}",
                    "invoice_id": invoice_ids[row.invoice_id], "source": source,
                })
                for row in payments if row.invoice_id in invoice_ids
            ]
            if payment_rows:
                db.execute(insert(Payment), payment_rows)
            db.commit()
            return source
        except IntegrityError:
            db.rollback()
            existing = db.get(DemoSession, canonical_id)
            if existing:
                return existing.source
            raise


@app.middleware("http")
async def demo_time_travel_context(request: Request, call_next):
    override = None
    if request.query_params.get("as_of"):
        try:
            override = date.fromisoformat(request.query_params["as_of"])
        except ValueError:
            pass
    source = None
    demo_session_id = request.headers.get("x-demo-session")
    if demo_session_id and not request.cookies.get(COOKIE_NAME):
        try:
            source = await asyncio.to_thread(_ensure_demo_session, demo_session_id)
        except (ValueError, RuntimeError, IntegrityError):
            return JSONResponse({"detail": "An isolated synthetic demo session could not be prepared."}, status_code=503)
    token = _REQUEST_DEMO_AS_OF.set(override)
    source_token = _REQUEST_DEMO_SOURCE.set(source)
    timings = {"db": 0.0, "model_load": 0.0, "scoring": 0.0, "drift": 0.0}
    timing_token = metrics.set(timings)
    started = perf_counter()
    try:
        response = await call_next(request)
        timings["total"] = (perf_counter() - started) * 1000
        response.headers["Server-Timing"] = ", ".join(f"{key};dur={value:.2f}" for key, value in timings.items())
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        if settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        logging.getLogger("uvicorn.error").info("%s %s %s", request.method, request.url.path, timings)
        return response
    finally:
        metrics.reset(timing_token)
        _REQUEST_DEMO_SOURCE.reset(source_token)
        _REQUEST_DEMO_AS_OF.reset(token)


app.add_middleware(PrivateReadCache)


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class SendInput(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class PaymentInput(BaseModel):
    amount: Decimal | None = None


class ReplyInput(BaseModel):
    reply: str = Field(min_length=1, max_length=4000)


class ReplyReviewInput(BaseModel):
    classification: Literal["promise_to_pay", "dispute", "question", "other"]
    promised_payment_date: date | None = None


class CollectionReviewInput(BaseModel):
    owner: str = Field(default="", max_length=128)
    notes: str = Field(default="", max_length=4000)
    follow_up_date: date | None = None
    resolution_status: Literal["open", "in_progress", "awaiting_response", "disputed", "resolved", "written_off"] = "open"


class ReviewMetricInput(BaseModel):
    review_seconds: int = Field(ge=1, le=3600)
    operation: Literal["approve", "skip"]


class AskInput(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class CreateInvoiceInput(BaseModel):
    client_id: str = Field(min_length=1, max_length=36)
    amount: Decimal = Field(gt=Decimal("0"), le=Decimal("1000000"))
    payment_term_type: Literal["DUE_ON_RECEIPT", "NET_15", "NET_30"]
    partial_payment_allowed: bool = False


def _sandbox_operation_stale(updated_at: datetime, now: datetime, after: timedelta = timedelta(minutes=2)) -> bool:
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return now - updated_at >= after


class WhatIfInput(BaseModel):
    payment_term_type: Literal["NET_15", "NET_30"] = "NET_15"


def _today(db: Session | None = None, source: str | None = None) -> date:
    if _is_demo_source(source) and _REQUEST_DEMO_AS_OF.get() is not None:
        return _REQUEST_DEMO_AS_OF.get()
    if db is not None and _is_demo_source(source):
        cache_key = f"demo_clock:{source}"
        if cache_key in db.info:
            return db.info[cache_key].as_of_date
        demo_state = db.get(DemoState, "demo") if source == "demo" else db.scalar(
            select(DemoSession).where(DemoSession.source == source)
        )
        if demo_state:
            db.info[cache_key] = demo_state
            return demo_state.as_of_date
    try:
        return datetime.now(ZoneInfo(settings.merchant_timezone)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(timezone.utc).date()


def _demo_as_of_max(db: Session, source: str) -> date:
    state = db.get(DemoState, "demo") if source == "demo" else db.scalar(
        select(DemoSession).where(DemoSession.source == source)
    )
    return state.as_of_date if state else _today(db)


def _historical_demo(db: Session, source: str) -> bool:
    override = _REQUEST_DEMO_AS_OF.get()
    if not _is_demo_source(source) or override is None:
        return False
    cache_key = f"demo_clock:{source}"
    state = db.info.get(cache_key)
    if state is None:
        state = db.get(DemoState, "demo") if source == "demo" else db.scalar(
            select(DemoSession).where(DemoSession.source == source)
        )
    if state:
        db.info[cache_key] = state
    return bool(state and override != state.as_of_date)


def _verify_login(username: str, password: str) -> bool:
    if not settings.production_auth_configured:
        return False
    expected_user = hmac.compare_digest(username.encode(), settings.operator_username.encode())
    expected_password = hmac.compare_digest(password.encode(), settings.operator_password.encode())
    return expected_user and expected_password


def _auth_attempts_remaining(ip_address: str, now: float | None = None) -> int:
    current = monotonic() if now is None else now
    with _AUTH_LOCK:
        failures = _AUTH_FAILURES.get(ip_address)
        if failures is None:
            return 5
        while failures and failures[0] <= current - 60:
            failures.popleft()
        if not failures:
            _AUTH_FAILURES.pop(ip_address, None)
            return 5
        return max(0, 5 - len(failures))


def _record_auth_failure(ip_address: str, now: float | None = None) -> None:
    current = monotonic() if now is None else now
    with _AUTH_LOCK:
        failures = _AUTH_FAILURES.setdefault(ip_address, deque())
        while failures and failures[0] <= current - 60:
            failures.popleft()
        failures.append(current)
        stale_keys = [key for key, attempts in _AUTH_FAILURES.items() if not attempts or attempts[-1] <= current - 60]
        for key in stale_keys:
            _AUTH_FAILURES.pop(key, None)
        overflow = len(_AUTH_FAILURES) - _MAX_AUTH_FAILURE_KEYS
        if overflow > 0:
            oldest = sorted(_AUTH_FAILURES, key=lambda key: _AUTH_FAILURES[key][-1])
            for key in oldest[:overflow]:
                _AUTH_FAILURES.pop(key, None)


def _clear_auth_failures(ip_address: str) -> None:
    with _AUTH_LOCK:
        _AUTH_FAILURES.pop(ip_address, None)


def _client_records(db: Session, client_id: str) -> list[dict]:
    portfolio = db.info.get(PORTFOLIO_CONTEXT_KEY)
    if portfolio is not None and client_id in portfolio["records_by_client"]:
        return portfolio["records_by_client"][client_id]
    rows = list(db.scalars(select(Invoice).where(Invoice.client_id == client_id)).all())
    ids = [row.id for row in rows]
    payments = list(db.scalars(select(Payment).where(Payment.invoice_id.in_(ids))).all()) if ids else []
    grouped: dict[str, list[Payment]] = {}
    for payment in payments:
        grouped.setdefault(payment.invoice_id, []).append(payment)
    return [_as_record(row, grouped.get(row.id, [])) for row in rows]


def _prepare_portfolio_context(
    db: Session, invoices: list[Invoice], *, include_policy: bool = True, include_predictions: bool = True,
) -> dict:
    """Load shared invoice facts once for a portfolio request instead of per invoice."""
    existing = db.info.get(PORTFOLIO_CONTEXT_KEY)
    if existing is not None:
        return existing

    invoice_rows = list(invoices)
    invoice_ids = [invoice.id for invoice in invoice_rows]
    invoices_by_client: dict[str, list[Invoice]] = {}
    for invoice in invoice_rows:
        invoices_by_client.setdefault(invoice.client_id, []).append(invoice)

    history_complete = bool(db.info.get("precomputing"))
    payments_by_invoice: dict[str, list[Payment]] = {}
    payment_counts: dict[str, int] = {}
    last_payment_by_invoice: dict[str, datetime] = {}
    partial_payment_clients: set[str] = set()
    records_by_client: dict[str, list[dict]] = {}
    if history_complete:
        payments = list(db.scalars(select(Payment).where(Payment.invoice_id.in_(invoice_ids))).all()) if invoice_ids else []
        for payment in payments:
            payments_by_invoice.setdefault(payment.invoice_id, []).append(payment)
            payment_counts[payment.invoice_id] = payment_counts.get(payment.invoice_id, 0) + 1
            previous = last_payment_by_invoice.get(payment.invoice_id)
            if previous is None or payment.occurred_at > previous:
                last_payment_by_invoice[payment.invoice_id] = payment.occurred_at
        records_by_client = {
            client_id: [_as_record(invoice, payments_by_invoice.get(invoice.id, [])) for invoice in client_invoices]
            for client_id, client_invoices in invoices_by_client.items()
        }
        partial_payment_clients = {invoice.client_id for invoice in invoice_rows if payment_counts.get(invoice.id, 0) > 1}
    elif include_policy:
        # Aggregate history in SQL rather than hydrating every historical payment.
        payment_facts = db.execute(select(Invoice.client_id, Payment.invoice_id, func.count(Payment.id), func.max(Payment.occurred_at))
            .join(Invoice, Payment.invoice_id == Invoice.id)
            .where(Invoice.source.in_({invoice.source for invoice in invoice_rows}))
            .group_by(Invoice.client_id, Payment.invoice_id)).all()
        for client_id, invoice_id, count, latest in payment_facts:
            if count > 1:
                partial_payment_clients.add(client_id)
            last_payment_by_invoice[invoice_id] = latest

    attempts_by_invoice: dict[str, list[tuple[ActionAttempt, str]]] = {}
    if include_policy and invoice_ids:
        attempt_rows = db.execute(
            select(ActionAttempt, Recommendation.invoice_id, Recommendation.tone)
            .join(Recommendation, ActionAttempt.recommendation_id == Recommendation.id)
            .where(Recommendation.invoice_id.in_(invoice_ids))
            .order_by(ActionAttempt.started_at.desc())
        ).all()
        for attempt, invoice_id, tone in attempt_rows:
            attempts_by_invoice.setdefault(invoice_id, []).append((attempt, tone))
    last_attempt_by_invoice: dict[str, tuple[datetime | None, bool, str | None, datetime | None]] = {}
    for invoice_id in invoice_ids if include_policy else []:
        recent = attempts_by_invoice.get(invoice_id, [])[:10]
        last = next((attempt.started_at for attempt, _ in recent if attempt.state in {"sent", "sending", "unknown"}), None)
        unresolved = any(attempt.state in {"sending", "unknown"} for attempt, _ in recent)
        last_tone = next((tone for attempt, tone in recent if last is not None and attempt.started_at == last), None)
        last_attempt_by_invoice[invoice_id] = (last, unresolved, last_tone, last_payment_by_invoice.get(invoice_id))

    latest_recommendation_by_invoice: dict[str, Recommendation] = {}
    if include_policy and invoice_ids:
        recommendations = db.scalars(
            select(Recommendation).where(Recommendation.invoice_id.in_(invoice_ids))
            .order_by(Recommendation.created_at.desc())
        ).all()
        for recommendation in recommendations:
            latest_recommendation_by_invoice.setdefault(recommendation.invoice_id, recommendation)

    scoring_dates = {_today(db, source) for source in {invoice.source for invoice in invoice_rows}}
    predictions_by_key = {}
    if include_predictions and invoice_ids and scoring_dates:
        predictions = db.scalars(
            select(Prediction).where(
                Prediction.invoice_id.in_(invoice_ids),
                Prediction.scoring_date.in_(scoring_dates),
            )
        ).all()
        predictions_by_key = {
            (prediction.invoice_id, prediction.scoring_date, prediction.fingerprint, prediction.model_version): prediction
            for prediction in predictions
        }

    projection_query = select(ReadModel).where(ReadModel.as_of.in_(scoring_dates), ReadModel.key != "demo:snapshot")
    if not history_complete:
        projection_query = projection_query.where(ReadModel.key.like("forecast_summary:%") | ReadModel.key.like("drift:%") | ReadModel.key.like("activity:%"))
    context = {
        "read_models": {(row.key, row.as_of): row for row in db.scalars(projection_query).all()},
        "invoices": invoice_rows,
        "invoices_by_client": invoices_by_client,
        "payments_by_invoice": payments_by_invoice,
        "records_by_client": records_by_client,
        "partial_payment_clients": partial_payment_clients if include_policy else set(),
        "last_attempt_by_invoice": last_attempt_by_invoice,
        "latest_recommendation_by_invoice": latest_recommendation_by_invoice,
        "predictions_by_key": predictions_by_key,
        "invoice_count_by_client": {client_id: len(rows) for client_id, rows in invoices_by_client.items()},
    }
    db.info[PORTFOLIO_CONTEXT_KEY] = context
    return context


def _activity_for_source(db: Session, source: str) -> dict[str, float]:
    if not db.info.get("precomputing"):
        cached = db.get(ReadModel, (f"activity:{source}", _today(db, source)))
        if cached:
            return cached.payload
    rows = list(db.execute(select(Invoice.client_id, Invoice.issue_date).where(
        Invoice.source == source, Invoice.status.not_in(ACTIVITY_EXCLUDED_STATUSES),
    )).all())
    issue_dates: dict[str, list[date]] = {}
    for client_id, issue_date in rows:
        issue_dates.setdefault(client_id, []).append(issue_date)
    as_of = _today(db, source)
    cache_key = (source, as_of, tuple(sorted((client_id, issue_date) for client_id, issue_date in rows)))
    with _ACTIVITY_CACHE_LOCK:
        fit = _ACTIVITY_CACHE.get(cache_key)
        if fit is None:
            fit = fit_bgnbd(issue_dates, as_of)
            if len(_ACTIVITY_CACHE) >= 8:
                _ACTIVITY_CACHE.clear()
            _ACTIVITY_CACHE[cache_key] = fit
    return fit.get("probabilities", {})


@timed("drift")
def _drift_for(db: Session, client: Client, activity_probability: float | None = None) -> dict:
    as_of = _today(db, client.source)
    if not db.info.get("precomputing"):
        context = db.info.get(PORTFOLIO_CONTEXT_KEY)
        cached = context["read_models"].get((f"drift:{client.id}", as_of)) if context else db.get(ReadModel, (f"drift:{client.id}", as_of))
        if cached:
            return cached.payload
    cadence = None
    records = _client_records(db, client.id)
    issued = sorted(row["issue_date"] for row in records)
    if len(issued) >= 5:
        gaps = [(issued[index] - issued[index - 1]).days for index in range(1, len(issued))]
        cadence = float(sorted(gaps)[len(gaps) // 2])
    open_by_currency: dict[str, Decimal] = {}
    historical_amounts: dict[str, list[Decimal]] = {}
    for row in records:
        currency = row["currency"]
        if row["status"] not in CLOSED_STATUSES and Decimal(str(row["outstanding"])) > 0:
            open_by_currency[currency] = open_by_currency.get(currency, Decimal("0")) + Decimal(str(row["outstanding"]))
        elif row["status"] == "PAID":
            historical_amounts.setdefault(currency, []).append(Decimal(str(row["total"])))
    risk: dict[str, float] = {}
    for currency in set(open_by_currency) | set(historical_amounts):
        amounts = sorted(historical_amounts.get(currency, []))
        expected_next = amounts[len(amounts) // 2] if amounts else Decimal("0")
        risk[currency] = float(open_by_currency.get(currency, Decimal("0")) + expected_next)
    drift = client_drift(
        records, _today(db, client.source), cadence_days=cadence,
        activity_probability=activity_probability, money_at_risk_by_currency=risk,
    )
    drift["money_at_risk_basis"] = "Open balances plus the median settled invoice amount, kept separate by currency."
    if not _historical_demo(db, client.source):
        client.drift = drift
        client.scored_at = datetime.now(timezone.utc)
    return drift


def _last_attempt(db: Session, invoice_id: str) -> tuple[datetime | None, bool, str | None, datetime | None]:
    portfolio = db.info.get(PORTFOLIO_CONTEXT_KEY)
    if portfolio is not None:
        return portfolio["last_attempt_by_invoice"].get(invoice_id, (None, False, None, None))
    attempts = list(db.scalars(
        select(ActionAttempt).join(Recommendation).where(Recommendation.invoice_id == invoice_id)
        .order_by(ActionAttempt.started_at.desc()).limit(10)
    ).all())
    last = next((attempt.started_at for attempt in attempts if attempt.state in {"sent", "sending", "unknown"}), None)
    unresolved = any(attempt.state in {"sending", "unknown"} for attempt in attempts)
    last_tone = None
    if last:
        previous = db.scalar(select(Recommendation).join(ActionAttempt).where(
            Recommendation.invoice_id == invoice_id, ActionAttempt.started_at == last,
        ))
        last_tone = previous.tone if previous else None
    last_payment = db.scalar(select(func.max(Payment.occurred_at)).where(Payment.invoice_id == invoice_id))
    return last, unresolved, last_tone, last_payment


def _latest_recommendation(db: Session, invoice_id: str) -> Recommendation | None:
    portfolio = db.info.get(PORTFOLIO_CONTEXT_KEY)
    if portfolio is not None:
        return portfolio["latest_recommendation_by_invoice"].get(invoice_id)
    return db.scalar(select(Recommendation).where(Recommendation.invoice_id == invoice_id)
                     .order_by(Recommendation.created_at.desc()).limit(1))


def _client_has_partial_history(db: Session, client_id: str) -> bool:
    portfolio = db.info.get(PORTFOLIO_CONTEXT_KEY)
    if portfolio is not None:
        return client_id in portfolio["partial_payment_clients"]
    invoice_ids = list(db.scalars(select(Invoice.id).where(Invoice.client_id == client_id)).all())
    if not invoice_ids:
        return False
    counts = db.execute(select(Payment.invoice_id, func.count(Payment.id)).where(
        Payment.invoice_id.in_(invoice_ids)
    ).group_by(Payment.invoice_id)).all()
    return any(count > 1 for _, count in counts)


def _client_history_facts(db: Session, client_id: str) -> dict:
    records = _client_records(db, client_id)
    settled = [row for row in records if row.get("settled_at")]
    delays = [(row["settled_at"] - row["due_date"]).days for row in settled]
    median_amounts: dict[str, float] = {}
    for currency in {row["currency"] for row in settled}:
        amounts = sorted(float(row["total"]) for row in settled if row["currency"] == currency)
        if amounts:
            median_amounts[currency] = round(float(statistics.median(amounts)), 2)
    invoice_ids = [row["id"] for row in records]
    payment_counts = db.execute(select(Payment.invoice_id, func.count(Payment.id)).where(
        Payment.invoice_id.in_(invoice_ids)
    ).group_by(Payment.invoice_id)).all() if invoice_ids else []
    partial_invoice_ids = {row["id"] for row in records if row["status"] == "PARTIALLY_PAID"}
    partial_invoice_ids.update(invoice_id for invoice_id, count in payment_counts if count > 1)
    partial_invoice_count = len(partial_invoice_ids)
    return {
        "invoice_count": len(records),
        "settled_invoice_count": len(settled),
        "median_days_late": round(float(statistics.median(delays)), 1) if delays else None,
        "partial_payment_invoice_count": partial_invoice_count,
        "median_invoice_amount_by_currency": median_amounts,
    }


def _client_stories(records: list[dict], as_of: date, invoice_numbers: dict[str, str]) -> list[dict]:
    stories: list[dict] = []
    cutoff = as_of - timedelta(days=180)
    older_terms = Counter(row.get("payment_term_type") for row in records
                          if row.get("issue_date") and row["issue_date"] < cutoff and row.get("payment_term_type"))
    recent_terms = Counter(row.get("payment_term_type") for row in records
                           if row.get("issue_date") and row["issue_date"] >= cutoff and row.get("payment_term_type"))
    if older_terms and recent_terms:
        previous = older_terms.most_common(1)[0][0]
        current = recent_terms.most_common(1)[0][0]
        if previous != current and previous in {"NET_30", "NET_15"} and current in {"NET_30", "NET_15"}:
            effective_date = min(row["issue_date"] for row in records
                                 if row.get("issue_date") and row["issue_date"] >= cutoff
                                 and row.get("payment_term_type") == current)
            previous_label = previous.replace("_", " ").title()
            current_label = current.replace("_", " ").title()
            representative = next(row for row in records if row.get("issue_date") and row["issue_date"] >= cutoff
                                  and row.get("payment_term_type") == current)
            stories.append({"key": "terms_shift", "text": f"Payment terms shifted from {previous_label} to {current_label} for invoices issued from {effective_date.isoformat()}; this is a historical association, not a causal effect.", "invoice_id": representative["id"], "invoice_number": invoice_numbers.get(representative["id"], "invoice")})

    for row in records:
        if not row.get("settled_at"):
            continue
        firm_events = [event_date(event.get("sent_at")) for event in row.get("reminder_events", [])
                       if isinstance(event, dict) and str(event.get("tone", "")).casefold() == "firm"]
        firm_sent = max((sent for sent in firm_events if sent and sent < row["settled_at"]), default=None)
        if firm_sent:
            days_after = (row["settled_at"] - firm_sent).days
            stories.append({"key": "firm_reminder", "text": f"{invoice_numbers.get(row['id'], 'An invoice')} settled {days_after} days after its recorded firm reminder; sequence does not establish causality.", "invoice_id": row["id"], "invoice_number": invoice_numbers.get(row["id"], "Invoice")})
            break

    for row in records:
        if row.get("disputed") and Decimal(str(row.get("outstanding", 0))) > 0:
            stories.append({"key": "dispute", "text": f"{invoice_numbers.get(row['id'], 'An open invoice')} has an active dispute; reminders are paused while it is reviewed.", "invoice_id": row["id"], "invoice_number": invoice_numbers.get(row["id"], "Invoice")})
            break
    return stories[:3]


def _draft_facts(invoice: Invoice, client: Client, forecast: dict | None, drift: dict, action: dict,
                 payment_url: str | None, history: dict) -> dict:
    return {
        "invoice": {
            "client_name": client.display_name,
            "invoice_number": invoice.invoice_number,
            "outstanding": f"{invoice.outstanding:.2f}",
            "currency": invoice.currency,
            "due_date": invoice.due_date.isoformat(),
            "payment_term_type": invoice.payment_term_type,
            "partial_payment_allowed": invoice.partial_payment_allowed,
            "payment_url": payment_url,
        },
        "tone": action["tone"],
        "policy_reason": action["reason"],
        "forecast_reasons": (forecast or {}).get("reasons", [])[:4],
        "client_signal": {
            "signal_type": drift.get("signal_type"),
            "message": drift.get("message"),
            "usual_delay_days": drift.get("usual_delay_days"),
            "recent_median_delay_days": drift.get("recent_median_delay_days"),
        },
        "client_history": history,
    }


def _why_line(forecast: dict | None, drift: dict, decision: dict | None = None) -> str:
    parts = []
    if drift.get("available") and drift.get("signal_type") in {"paying_later", "later_than_usual", "gone_quiet"}:
        message = str(drift.get("message") or "").strip()
        if message:
            parts.append(message if message.endswith((".", "!", "?")) else f"{message}.")
    probability = (forecast or {}).get("probabilities", {}).get("14d")
    if probability is not None:
        parts.append(f"The forecast estimates a {probability:.0%} chance of full payment within 14 days.")
    policy_reason = str((decision or {}).get("reason") or "").strip()
    if policy_reason:
        if not policy_reason.endswith((".", "!", "?")):
            policy_reason += "."
        parts.append(f"Policy: {policy_reason}")
    if not parts:
        reasons = (forecast or {}).get("reasons") or []
        if reasons:
            sentence = str(reasons[0]).strip()
            parts.append(sentence if sentence.endswith((".", "!", "?")) else f"{sentence}.")
    return " ".join(parts) or "This forecast uses the portfolio baseline because no invoice-specific driver is available."


def _reminder_outcomes(db: Session, invoice: Invoice) -> list[dict]:
    """Summarize observed payment outcomes in the seven days after each reminder."""
    events = []
    for event in invoice.reminder_events or []:
        if not isinstance(event, dict):
            continue
        sent = event_date(event.get("sent_at"))
        if sent:
            events.append((sent, event))
    events.sort(key=lambda item: item[0])
    if not events:
        return []
    # Detail outcomes must reflect the current transaction, including payments
    # recorded after a request-scoped portfolio snapshot was assembled.
    payments = list(
        db.scalars(select(Payment).where(Payment.invoice_id == invoice.id).order_by(Payment.occurred_at)).all()
    )
    payment_days = [(business_date(payment.occurred_at), payment.amount) for payment in payments]
    today = _today(db, invoice.source)
    output = []
    for index, (sent, event) in enumerate(events):
        observed_end = sent + timedelta(days=7)
        if index + 1 < len(events):
            observed_end = min(observed_end, events[index + 1][0])
        amount = sum((value for paid_at, value in payment_days if sent < paid_at <= observed_end), Decimal("0"))
        mature = today >= observed_end
        state = "paid" if amount >= invoice.total else "partial" if amount > 0 else "silent" if mature else "checking"
        output.append({
            "sent_at": sent.isoformat(),
            "tone": str(event.get("tone") or "reminder"),
            "status": state,
            "amount_paid": str(min(invoice.total, amount).quantize(Decimal("0.01"))),
            "currency": invoice.currency,
            "observed_through": min(today, observed_end).isoformat(),
            "window_ends": observed_end.isoformat(),
            "window_days": max(0, (observed_end - sent).days),
        })
    return output


@lru_cache(maxsize=4)
def _backtest_issue_predictions(path: str) -> dict[str, dict]:
    try:
        value = json.loads(Path(path).read_text())
        rows = value.get("backtest", {}).get("invoices", [])
        model_version = value.get("model_version")
        model_label = (value.get("model_card") or {}).get("deployed_model")
        return {
            str(row["invoice_id"]): {
                **row, "model_version": row.get("model_version") or model_version,
                "model_label": row.get("model_label") or model_label,
            }
            for row in rows if row.get("invoice_id")
        }
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def _issue_prediction(db: Session, invoice: Invoice) -> dict | None:
    saved = db.scalar(select(Prediction).where(
        Prediction.invoice_id == invoice.id, Prediction.scoring_date == invoice.issue_date,
    ).limit(1))
    if saved:
        return {
            "source": "stored_issue_prediction", "model_version": saved.model_version,
            "model_label": saved.model_label,
            "p10_date": (saved.quantiles.get("p10") or {}).get("date"),
            "p50_date": (saved.quantiles.get("p50") or {}).get("date"),
            "p90_date": (saved.quantiles.get("p90") or {}).get("date"),
            "probabilities": saved.probabilities,
        }
    if not _is_demo_source(invoice.source):
        return None
    artifact = Path(settings.model_path).with_name("eval.json")
    row = _backtest_issue_predictions(str(artifact)).get(invoice.id)
    if not row:
        return None
    return {
        "source": "out_of_sample_issue_backtest", "model_version": row.get("model_version"),
        "model_label": row.get("model_label"),
        "p10_date": row.get("p10_date"), "p50_date": row.get("predicted_p50_date"),
        "p90_date": row.get("p90_date"), "probabilities": None,
        "backtest_actual_paid_date": row.get("actual_paid_date"),
        "absolute_error_days": row.get("absolute_error_days"),
        "within_p10_p90": row.get("within_p10_p90"),
    }


def _paid_outcome(db: Session, invoice: Invoice) -> dict | None:
    if invoice.status != "PAID":
        return None
    payments = list(db.scalars(select(Payment).where(Payment.invoice_id == invoice.id).order_by(Payment.occurred_at.asc())).all())
    paid_date = max((business_date(payment.occurred_at) for payment in payments), default=None)
    if paid_date is None and invoice.paypal_id:
        verified_paid_events = list(db.scalars(select(InvoiceEvent).where(
            InvoiceEvent.verified.is_(True), InvoiceEvent.event_type.ilike("%PAID%"),
        )).all())
        paid_dates = [business_date(event.occurred_at) for event in verified_paid_events
                      if event.occurred_at and _payload_mentions(event.raw_payload, {invoice.paypal_id})]
        paid_date = max(paid_dates, default=None)
    issue_prediction = _issue_prediction(db, invoice)
    if paid_date is None and issue_prediction and issue_prediction.get("backtest_actual_paid_date"):
        paid_date = date.fromisoformat(issue_prediction["backtest_actual_paid_date"])
    if issue_prediction and paid_date:
        predicted = date.fromisoformat(issue_prediction["p50_date"]) if issue_prediction.get("p50_date") else None
        p10 = date.fromisoformat(issue_prediction["p10_date"]) if issue_prediction.get("p10_date") else None
        p90 = date.fromisoformat(issue_prediction["p90_date"]) if issue_prediction.get("p90_date") else None
        issue_prediction["absolute_error_days"] = abs((paid_date - predicted).days) if predicted else None
        issue_prediction["within_p10_p90"] = bool(p10 and p90 and p10 <= paid_date <= p90)
    days_from_due = (paid_date - invoice.due_date).days if paid_date else None
    if days_from_due is None:
        timing = "date_unavailable"
    elif days_from_due > 0:
        timing = "late"
    elif days_from_due < 0:
        timing = "early"
    else:
        timing = "on_time"
    return {
        "paid_at": paid_date.isoformat() if paid_date else None,
        "days_from_due": days_from_due,
        "timing": timing,
        "issue_forecast": issue_prediction,
    }


def _timeline_at(value) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc).isoformat()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            try:
                return datetime.combine(date.fromisoformat(value[:10]), datetime.min.time(), tzinfo=timezone.utc).isoformat()
            except ValueError:
                return None
    return None


def _payload_mentions(value, invoice_ids: set[str]) -> bool:
    if isinstance(value, dict):
        return any(_payload_mentions(item, invoice_ids) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_payload_mentions(item, invoice_ids) for item in value)
    return isinstance(value, str) and value in invoice_ids


def _invoice_timeline(db: Session, invoice: Invoice) -> list[dict]:
    events: list[dict] = []

    def add(kind: str, at, detail: str | None = None) -> None:
        occurred = _timeline_at(at)
        if occurred:
            events.append({"kind": kind, "at": occurred, "detail": detail})

    add("Issued", invoice.issue_date)
    if _is_demo_source(invoice.source):
        add("Sent", invoice.issue_date, "Synthetic invoice delivery is modeled on the issue date.")
    if invoice.viewed_at:
        add("Viewed by recipient", invoice.viewed_at)
    elif invoice.viewed_by_recipient and invoice.viewed_observed_at:
        add("Viewed by recipient", invoice.viewed_observed_at, "PayPal confirms the invoice was viewed; its exact view time is unavailable.")
    for reminder in invoice.reminder_events or []:
        if isinstance(reminder, dict):
            tone = str(reminder.get("tone") or "").strip()
            add("Reminder sent", reminder.get("sent_at"), f"{tone.title()} tone" if tone else None)
    attempts = list(db.execute(select(ActionAttempt, Recommendation.tone).join(
        Recommendation, ActionAttempt.recommendation_id == Recommendation.id,
    ).where(Recommendation.invoice_id == invoice.id).order_by(ActionAttempt.started_at.asc())).all())
    for attempt, tone in attempts:
        label = "Reminder sent" if attempt.state == "sent" else f"Reminder {attempt.state}"
        add(label, attempt.started_at, f"{tone.title()} tone" if tone else None)
    payments = list(db.scalars(select(Payment).where(Payment.invoice_id == invoice.id).order_by(Payment.occurred_at.asc())).all())
    for index, payment in enumerate(payments):
        if invoice.status == "PAID" and index == len(payments) - 1:
            label = "Paid"
        elif payment.provider_payment_id.startswith("demo-partial-") or invoice.status == "PARTIALLY_PAID":
            label = "Partial payment"
        else:
            label = "Payment recorded"
        add(label, payment.occurred_at, f"{payment.currency} {payment.amount:,.2f}")
    if invoice.paypal_id:
        provider_ids = {invoice.paypal_id}
        verified_events = list(db.scalars(select(InvoiceEvent).where(InvoiceEvent.verified.is_(True), (InvoiceEvent.invoice_id == invoice.id) | InvoiceEvent.invoice_id.is_(None))).all())
        for event in verified_events:
            if not _payload_mentions(event.raw_payload, provider_ids):
                continue
            event_type = event.event_type.upper()
            label = "Sent by PayPal" if "SENT" in event_type else event.event_type.replace("INVOICING.INVOICE.", "").replace("_", " ").title()
            add(label, event.occurred_at or event.received_at)
    audits = list(db.scalars(select(AIAudit).where(
        AIAudit.invoice_id == invoice.id,
        AIAudit.kind.in_(["draft_edit", "reply_triage", "collection_review", "action_transition"]),
    ).order_by(AIAudit.created_at.asc())).all())
    for audit in audits:
        actor = audit.facts.get("actor") or "system"
        channel = audit.facts.get("channel") or "web"
        if audit.kind == "draft_edit":
            before_text = audit.facts.get("before_text", "")
            after_text = audit.output.get("text", "")
            detail = f"Internal note edited by {actor} via {channel}; saved text changed from {before_text!r} to {after_text!r}"
            add("Internal collection note changed", audit.created_at, detail)
        elif audit.kind == "reply_triage":
            state = audit.output.get("state", "preview")
            classification = audit.output.get("classification", "unknown").replace("_", " ")
            promised = audit.output.get("promised_payment_date")
            detail = f"{state} · {classification}" + (f" · promised {promised}" if promised else "") + f" · actor {actor} · channel {channel}"
            add("Client reply reviewed", audit.created_at, detail)
        elif audit.kind == "collection_review":
            before = audit.facts.get("before", {})
            after = audit.output.get("after", {})
            detail = (f"{before.get('resolution_status', 'open')} → {after.get('resolution_status', 'open')} · "
                      f"owner {before.get('owner', 'unassigned') or 'unassigned'} → {after.get('owner', 'unassigned') or 'unassigned'} · "
                      f"follow-up {after.get('follow_up_date') or 'none'} · actor {actor} · channel {channel} · "
                      f"notes changed: {before.get('notes', '')!r} → {after.get('notes', '')!r}")
            add("Collections review updated", audit.created_at, detail)
        else:
            before_state = audit.facts.get("before_state", "unknown")
            after_state = audit.output.get("state", "unknown")
            action = audit.output.get("action", "recommendation")
            add("Action state changed", audit.created_at,
                f"{action} · {before_state} → {after_state} · actor {actor} · channel {channel}")
    events.sort(key=lambda item: (item["at"], item["kind"]))
    return events


def _invoice_dict(
    db: Session,
    invoice: Invoice,
    include_detail: bool = False,
    include_timeline: bool = True,
    drift_cache: dict[str, dict] | None = None,
    activity_scores: dict[str, float] | None = None,
    daily_candidate_ids: set[str] | None = None,
    forecast_cache: dict[str, dict] | None = None,
    create_recommendation: bool = True,
) -> dict:
    client = invoice.client or db.get(Client, invoice.client_id)
    if _historical_demo(db, invoice.source):
        create_recommendation = False
    today = _today(db, invoice.source)
    outstanding = Decimal("0.00") if invoice.status in CLOSED_STATUSES else invoice.outstanding
    forecast = None
    if invoice.status not in CLOSED_STATUSES and invoice.outstanding > 0:
        forecast = None if include_detail and include_timeline else (forecast_cache or {}).get(invoice.id)
        if forecast is None:
            forecast = forecast_invoice(db, invoice, today=today, persist=True, detail=include_detail and include_timeline)
            if forecast_cache is not None and not include_detail:
                forecast_cache[invoice.id] = forecast
    if drift_cache is not None and client.id in drift_cache:
        drift = drift_cache[client.id]
    else:
        drift = _drift_for(db, client, (activity_scores or {}).get(client.id))
        if drift_cache is not None:
            drift_cache[client.id] = drift
    if forecast:
        forecast_reasons = [reason for reason in forecast.get("reasons", [])
                            if not str(reason).startswith("Recent settled invoices had a median delay")]
        recent_delay = drift.get("recent_median_delay_days")
        usual_delay = drift.get("usual_delay_days")
        if recent_delay is not None and usual_delay is not None:
            forecast_reasons.append(
                f"Client timing signal uses recent median observed delay of {recent_delay:g} days versus a usual {usual_delay:g} days."
            )
        forecast["reasons"] = forecast_reasons
    last_attempt, unresolved, last_tone, last_payment = _last_attempt(db, invoice.id) if _payable(invoice) else (None, False, None, None)
    action = decide(
        invoice, forecast["probability_by_due"] if forecast else None,
        last_attempt, unresolved, today,
        forecast.get("probabilities", {}).get("14d") if forecast else None,
        last_payment, last_tone, _client_has_partial_history(db, client.id) if _payable(invoice) else False,
    )
    action["holdback"] = False
    if action["action"] in {"friendly", "firm", "offer_partial_payment"}:
        action["holdback"] = holdback_assignment(invoice.id, today)
        if action["holdback"]:
            action["held_action"] = action["action"]
            action["action"] = "wait"
            action["tone"] = "none"
            action["reason"] = "This invoice is in the 10% policy holdback group for this date."
    if daily_candidate_ids is not None and action["action"] in {"friendly", "firm", "offer_partial_payment"} and invoice.id not in daily_candidate_ids:
        action["action_before_daily_cap"] = action["action"]
        action["action"] = "wait"
        action["tone"] = "none"
        action["reason"] = "The daily review capacity is full; this invoice is outside today's top five ranked by assumed 14-day cash uplift (10% scenario, not measured incremental cash)."
    recommendation = _latest_recommendation(db, invoice.id)
    skipped_for_current_policy = bool(
        recommendation
        and recommendation.state == "skipped"
        and recommendation.invoice_version == invoice.material_version
        and recommendation.policy_date in {None, today}
    )
    if recommendation and include_detail and create_recommendation and recommendation.policy_date is None:
        recommendation.policy_date = today
    if invoice.disputed or (invoice.promised_payment_date and today <= invoice.promised_payment_date):
        recommendation = None
    elif action["action"] == "review_for_writeoff_or_collections" and recommendation and recommendation.state == "proposed":
        recommendation = None
    if (include_detail and create_recommendation and recommendation and recommendation.state == "proposed"
            and (recommendation.invoice_version != invoice.material_version or recommendation.action != action["action"] or recommendation.policy_date != today)):
        recommendation.state = "superseded"
        db.flush()
        recommendation = None
    if include_detail and create_recommendation and not skipped_for_current_policy and (not recommendation or recommendation.invoice_version != invoice.material_version or recommendation.state in {"skipped", "sent", "failed", "applied", "superseded"}) and action["action"] in {"friendly", "firm", "offer_partial_payment"}:
        provider_url = ((invoice.provider_payload.get("detail") or {}).get("metadata") or {}).get("recipient_view_url")
        if not isinstance(provider_url, str) or urlparse(provider_url).scheme != "https" or not urlparse(provider_url).hostname or not urlparse(provider_url).hostname.endswith("paypal.com"):
            provider_url = None
        body = (
            "Approval will enable partial payments on this PayPal invoice. No reminder message will be sent."
            if action["action"] == "offer_partial_payment"
            else render_message(client.display_name, invoice.outstanding, invoice.currency, invoice.due_date, action["tone"], provider_url)
        )
        values = dict(
            invoice_id=invoice.id, action=action["action"], tone=action["tone"], reason=action["reason"],
            draft_text=body, draft_source="policy" if action["action"] == "offer_partial_payment" else "template", edited_text=body, invoice_version=invoice.material_version,
            policy_step=action.get("policy_step"), policy_date=today,
        )
        insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
        db.execute(insert(Recommendation).values(**values).on_conflict_do_nothing(
            index_elements=["invoice_id", "invoice_version"], index_where=text("state = 'proposed'")))
        recommendation = db.scalar(select(Recommendation).where(Recommendation.invoice_id == invoice.id,
            Recommendation.invoice_version == invoice.material_version, Recommendation.state == "proposed"))
    currency_due = max(0, (invoice.due_date - today).days)
    days_overdue = max(0, (today - invoice.due_date).days)
    usual_delay = drift.get("usual_delay_days")
    outlier_threshold = max(30, float(usual_delay or 0) + 21, float(usual_delay or 0) * 3)
    outlier_hint = None
    if _has_open_balance(invoice) and days_overdue >= outlier_threshold:
        outlier_hint = (
            f"This invoice is at least {days_overdue} days overdue, far beyond this client’s usual "
            f"{float(usual_delay):g}-day delay. Check whether it is disputed or the invoice was lost."
            if usual_delay is not None else
            f"This invoice is at least {days_overdue} days overdue. Check whether it is disputed or the invoice was lost."
        )
    summary = {
        "id": invoice.id, "paypal_id": invoice.paypal_id, "invoice_number": invoice.invoice_number,
        "client_id": client.id, "client_name": client.display_name, "client_email": client.email,
        "issue_date": invoice.issue_date.isoformat(), "due_date": invoice.due_date.isoformat(),
        "days_to_due": (invoice.due_date - today).days,
        "total": str(invoice.total), "paid": str(invoice.paid), "outstanding": str(outstanding),
        "currency": invoice.currency, "status": invoice.status, "disputed": invoice.disputed,
        "promised_payment_date": invoice.promised_payment_date.isoformat() if invoice.promised_payment_date else None,
        "reminder_step": invoice.reminder_step,
        "simulation_disabled": _historical_demo(db, invoice.source),
        "source": invoice.source, "material_version": invoice.material_version,
        "synced_at": invoice.synced_at.isoformat() if invoice.synced_at else None,
        "viewed_at": invoice.viewed_at.isoformat() if invoice.viewed_at else None,
        "viewed_by_recipient": bool(invoice.viewed_at or invoice.viewed_by_recipient),
        "viewed_observed_at": invoice.viewed_observed_at.isoformat() if invoice.viewed_observed_at else None,
        "partial_payment_allowed": invoice.partial_payment_allowed,
        "payment_term_type": invoice.payment_term_type,
        "reminder_events": invoice.reminder_events or [],
        "payment_url": (((invoice.provider_payload.get("detail") or {}).get("metadata") or {}).get("recipient_view_url")) if invoice.source == "paypal" else None,
        "forecast": forecast, "drift": drift, "decision": action,
        "outlier_hint": outlier_hint,
        "why": _why_line(forecast, drift, action),
        "recommendation": _recommendation_dict(recommendation),
        "days_until_due_nonnegative": currency_due,
    }
    if include_detail:
        summary["reminder_outcomes"] = _reminder_outcomes(db, invoice)
        triages = list(db.scalars(select(ReplyTriage).where(
            ReplyTriage.invoice_id == invoice.id,
        ).order_by(ReplyTriage.created_at.desc()).limit(5)).all())
        summary["reply_triages"] = [{
            "id": row.id, "reply_text": row.reply_text, "classification": row.classification,
            "promised_payment_date": row.promised_payment_date.isoformat() if row.promised_payment_date else None,
            "classifier": row.classifier, "state": row.state,
            "created_at": row.created_at.isoformat(),
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        } for row in triages]
    if include_detail and include_timeline:
        summary["outcome"] = _paid_outcome(db, invoice)
        summary["timeline"] = _invoice_timeline(db, invoice)
        attempts = list(db.scalars(select(ActionAttempt).where(
            ActionAttempt.recommendation_id == recommendation.id if recommendation else text("1=0")
        ).order_by(ActionAttempt.started_at.desc())).all()) if recommendation else []
        summary["attempts"] = [{
            "state": attempt.state, "provider_reference": attempt.provider_reference,
            "started_at": attempt.started_at.isoformat(), "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
            "error": attempt.error,
        } for attempt in attempts]
        summary["reminder_delivery_note"] = (
            "Demo only: this records an approval simulation. No PayPal email is sent."
            if _is_demo_source(invoice.source)
            else "Recipient delivery preview: PayPal sends its system-generated reminder to the invoice recipient for this invoice, with PayPal's hosted invoice and payment details. PayPal controls the subject and body; the editable text below is an internal collection note and is not transmitted."
        )
    if not include_detail and summary["forecast"]:
        summary["forecast"] = {key: value for key, value in summary["forecast"].items()
                               if key in {"quantiles", "probabilities", "model", "model_version", "limited_history", "display_label", "collection_probability", "weekly_settlement_probabilities"}}
    return summary


def _recommendation_dict(recommendation: Recommendation | None) -> dict | None:
    if not recommendation:
        return None
    return {
        "id": recommendation.id, "action": recommendation.action, "tone": recommendation.tone,
        "reason": recommendation.reason, "draft_text": recommendation.draft_text,
        "draft_source": recommendation.draft_source, "draft_model": recommendation.draft_model,
        "drafted_at": recommendation.drafted_at.isoformat() if recommendation.drafted_at else None,
        "edited_text": recommendation.edited_text, "invoice_version": recommendation.invoice_version,
        "state": recommendation.state, "approved_text": recommendation.approved_text,
        "approved_at": recommendation.approved_at.isoformat() if recommendation.approved_at else None,
        "policy_step": recommendation.policy_step,
    }


def _get_invoice_or_404(db: Session, invoice_id: str) -> Invoice:
    invoice = db.scalar(select(Invoice).options(joinedload(Invoice.client)).where(Invoice.id == invoice_id))
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return invoice


def _authorize_invoice(request: Request, invoice: Invoice, db: Session) -> str | None:
    user = session_user(request, db=db)
    if not user and invoice.source != _public_demo_source():
        raise HTTPException(status_code=401, detail="Operator login required to view PayPal invoices.")
    if request.method not in {"GET", "HEAD"} and _historical_demo(db, invoice.source):
        raise HTTPException(409, "Return the demo clock to today before changing records.")
    return user


def _payable(invoice: Invoice) -> bool:
    return invoice.status not in CLOSED_STATUSES and not invoice.disputed and invoice.outstanding > 0


def _has_open_balance(invoice: Invoice) -> bool:
    return invoice.status not in CLOSED_STATUSES and invoice.outstanding > 0


def _rank_policy_candidates(db: Session, source: str | None = None, forecast_cache: dict[str, dict] | None = None) -> list[str]:
    portfolio = db.info.get(PORTFOLIO_CONTEXT_KEY)
    if portfolio is not None:
        rows = portfolio["invoices"]
        if source:
            rows = [row for row in rows if row.source == source]
    else:
        query = select(Invoice).options(joinedload(Invoice.client)).where(Invoice.status.not_in(CLOSED_STATUSES), Invoice.outstanding > 0).order_by(Invoice.due_date.asc())
        if source:
            query = query.where(Invoice.source == source)
        rows = list(db.scalars(query).all())
        _prepare_portfolio_context(db, rows)
    candidates = []
    for invoice in rows:
        if not _payable(invoice):
            continue
        invoice_today = _today(db, invoice.source)
        forecast = (forecast_cache or {}).get(invoice.id)
        if forecast is None:
            forecast = forecast_invoice(db, invoice, today=invoice_today, persist=True, detail=False)
            if forecast_cache is not None:
                forecast_cache[invoice.id] = forecast
        last_attempt, unresolved, last_tone, last_payment = _last_attempt(db, invoice.id)
        action = decide(
            invoice, forecast.get("probability_by_due"), last_attempt, unresolved, invoice_today,
            forecast.get("probabilities", {}).get("14d"), last_payment, last_tone,
            _client_has_partial_history(db, invoice.client_id),
        )
        if action["action"] in {"friendly", "firm", "offer_partial_payment"} and not holdback_assignment(invoice.id, invoice_today):
            candidates.append((invoice.currency, action["expected_cash_gain"], invoice.due_date, invoice.id))

    # Currency amounts cannot be compared without conversion. Rank within each
    # currency, then round-robin the lanes so the daily cap is shared.
    by_currency: dict[str, list[tuple[float, date, str]]] = {}
    for currency, score, due_date, invoice_id in candidates:
        by_currency.setdefault(currency, []).append((score, due_date, invoice_id))
    for values in by_currency.values():
        values.sort(key=lambda row: (-row[0], row[1], row[2]))
    ranked: list[str] = []
    currencies = sorted(by_currency)
    while any(by_currency.values()):
        for currency in currencies:
            if by_currency[currency]:
                ranked.append(by_currency[currency].pop(0)[2])
    return ranked


def _pending_policy_candidate_ids(db: Session, ranked_ids: list[str], sources: list[str]) -> list[str]:
    """Return the queue's current candidates before per-invoice review rows exist."""
    reviewed_today: set[str] = set()
    for source in sources:
        today = _today(db, source)
        reviewed_today.update(db.scalars(select(Recommendation.invoice_id).join(Invoice).where(
            Invoice.source == source, Recommendation.state.in_(["skipped", "sent", "applied"]),
            Recommendation.policy_date == today,
        )).all())
    return [invoice_id for invoice_id in ranked_ids if invoice_id not in reviewed_today]


def _top_policy_candidates(db: Session, source: str | None = None) -> set[str]:
    query = select(Invoice).where(
        Invoice.status.not_in(CLOSED_STATUSES), Invoice.outstanding > 0,
    ).order_by(Invoice.id)
    if source:
        query = query.where(Invoice.source == source)
    rows = list(db.scalars(query).all())
    dates = tuple(sorted({(row.source, _today(db, row.source).isoformat()) for row in rows}))
    facts = tuple((
        row.id, row.material_version, row.status, str(row.outstanding), row.due_date.isoformat(),
        row.reminder_step, row.disputed,
        row.promised_payment_date.isoformat() if row.promised_payment_date else None,
        row.partial_payment_allowed, row.payment_term_type,
    ) for row in rows)
    cache_key = (source, dates, facts)
    with _POLICY_CANDIDATE_CACHE_LOCK:
        cached = _POLICY_CANDIDATE_CACHE.get(cache_key)
    if cached is not None:
        return set(cached)
    result = frozenset(_rank_policy_candidates(db, source)[:DAILY_SUGGESTION_CAP])
    with _POLICY_CANDIDATE_CACHE_LOCK:
        _POLICY_CANDIDATE_CACHE[cache_key] = result
        if len(_POLICY_CANDIDATE_CACHE) > 128:
            _POLICY_CANDIDATE_CACHE.pop(next(iter(_POLICY_CANDIDATE_CACHE)))
    return set(result)


def _record_policy_decision(db: Session, invoice: Invoice, summary: dict) -> None:
    if _historical_demo(db, invoice.source):
        return
    decision = summary["decision"]
    forecast = summary.get("forecast") or {}
    fields = {
        "invoice_material_version": invoice.material_version,
        "decision_date": _today(db, invoice.source).isoformat(),
        "days_to_due": summary["days_to_due"],
        "outstanding": summary["outstanding"],
        "currency": invoice.currency,
        "probability_by_due": forecast.get("probability_by_due"),
        "probability_14d": forecast.get("probabilities", {}).get("14d"),
        "probability_if_wait": decision.get("probability_if_wait"),
        "probability_if_sent": decision.get("probability_if_sent"),
        "expected_cash_gain": decision.get("expected_cash_gain"),
        "reminder_step": invoice.reminder_step,
        "policy_step": decision.get("policy_step"),
    }
    payload = {"invoice_id": invoice.id, "features": fields, "action": decision["action"], "reason": decision["reason"],
               "holdback": decision.get("holdback", False)}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    options = ["friendly", "firm", "offer_partial_payment", "personal_call", "wait"]
    alternatives = [{"action": name, "selected": name == decision["action"]} for name in options]
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    db.execute(insert(PolicyDecisionLog).values(
        invoice_id=invoice.id, decision_date=date.fromisoformat(fields["decision_date"]), fingerprint=fingerprint,
        features=fields, chosen_action=decision["action"], alternatives=alternatives,
        assigned_holdback=bool(decision.get("holdback")), outcome={"status": "pending"},
    ).on_conflict_do_nothing(index_elements=["fingerprint"]))


def _update_policy_outcomes(db: Session, invoice_id: str, outcome: dict) -> None:
    logs = list(db.scalars(select(PolicyDecisionLog).where(
        PolicyDecisionLog.invoice_id == invoice_id,
        PolicyDecisionLog.outcome["status"].as_string() == "pending",
    )).all())
    for entry in logs:
        entry.outcome = {**(entry.outcome or {}), **outcome}


def _check_policy(db: Session, invoice: Invoice, forecast: dict | None, source_scope: str | None = None) -> None:
    last_attempt, unresolved, last_tone, last_payment = _last_attempt(db, invoice.id)
    action = decide(
        invoice, forecast["probability_by_due"] if forecast else None, last_attempt, unresolved,
        _today(db, invoice.source), forecast.get("probabilities", {}).get("14d") if forecast else None,
        last_payment, last_tone, _client_has_partial_history(db, invoice.client_id),
    )
    action["holdback"] = holdback_assignment(invoice.id, _today(db, invoice.source))
    if action["holdback"] and action["action"] in {"friendly", "firm", "offer_partial_payment"}:
        raise HTTPException(status_code=409, detail="This invoice is assigned to today's policy holdback group.")
    if action["action"] not in {"friendly", "firm"}:
        raise HTTPException(status_code=409, detail=action["reason"])
    if invoice.id not in _top_policy_candidates(db, source_scope):
        raise HTTPException(status_code=409, detail="This invoice is outside today's top five suggestions ranked by assumed 14-day cash uplift (10% scenario, not measured incremental cash).")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/healthz")
def healthz() -> dict:
    """Minimal liveness probe; it exposes no deployment or credential state."""
    return {"ok": True}


@app.get("/ai/status")
def ai_status() -> dict:
    from app.services.ai import GEMINI_MODEL
    configured = bool(settings.gemini_api_key)
    return {"provider": "Google Gemini", "model": GEMINI_MODEL, "configured": configured,
            "status": "configured" if configured else "not_configured"}


@app.post("/demo/reset")
def reset_demo(db: Session = Depends(get_db)) -> dict:
    if _public_demo_source() != "demo":
        raise HTTPException(410, "Reset the isolated browser demo from the portfolio; the shared base snapshot is protected.")
    state = db.scalar(select(DemoState).where(DemoState.id == "demo").with_for_update())
    if not state:
        raise HTTPException(status_code=503, detail="The synthetic demo is not seeded yet.")
    now = datetime.now(timezone.utc)
    last_reset = state.reset_completed_at
    if last_reset and last_reset.tzinfo is None:
        last_reset = last_reset.replace(tzinfo=timezone.utc)
    if last_reset and (now - last_reset).total_seconds() < 300:
        seconds_left = max(1, 300 - int((now - last_reset).total_seconds()))
        raise HTTPException(status_code=429, detail=f"The demo was reset recently. Try again in about {seconds_left // 60 + 1} minute(s).")
    state.reset_completed_at = now
    db.commit()
    try:
        result = _seed_demo(state.as_of_date)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="The demo reset failed; try again after the cooldown.") from exc
    db.refresh(state)
    return {"reset": True, "cutoff": result["cutoff"], "clients": result["clients"],
            "invoices": result["invoices"], "open_count": result["open_count"],
            "message": "Synthetic demo state was restored. PayPal Sandbox records were not changed."}


def _seed_demo(as_of: date) -> dict:
    from scripts.seed_demo import seed
    from app.db import SessionLocal
    from app.services.demo_snapshot import restore
    with SessionLocal() as snapshot_db:
        result = restore(snapshot_db, as_of)
        if result:
            return result
    return seed(as_of=as_of)


@app.get("/evaluation")
def model_evaluation() -> dict:
    path = Path(settings.model_path).with_name("eval.json")
    if not path.exists():
        return {"available": False, "reason": "Model evaluation has not been generated yet."}
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"available": False, "reason": "Model evaluation artifact could not be read."}
    importance_path = path.with_name("feature_importance.json")
    try:
        importance = json.loads(importance_path.read_text())
        payload["feature_importance"] = importance.get("features", [])
        payload["feature_importance_method"] = importance.get("method", "Unavailable")
    except (OSError, ValueError, AttributeError):
        payload["feature_importance"] = []
        payload["feature_importance_method"] = "Unavailable"
    shap_path = path.with_name("shap_summary.json")
    if shap_path.exists():
        payload["shap_summary"] = json.loads(shap_path.read_text()).get("features", [])
    public_path = path.with_name("public_eval.json")
    try:
        payload["public_evaluation"] = json.loads(public_path.read_text()) if public_path.exists() else None
    except (OSError, ValueError):
        payload["public_evaluation"] = None
    payload["available"] = True
    return payload


@app.get("/auth/me")
def auth_me(request: Request, db: Session = Depends(get_db)) -> dict:
    user = session_user(request, db=db)
    return {"authenticated": bool(user), "username": user}


@app.post("/auth/login")
def login(payload: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    ip_address = request.client.host if request.client else "unknown"
    if _auth_attempts_remaining(ip_address) == 0:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again in one minute.", headers={"Retry-After": "60"})
    if not _verify_login(payload.username, payload.password):
        _record_auth_failure(ip_address)
        raise HTTPException(status_code=401, detail="Username or password is incorrect.")
    _clear_auth_failures(ip_address)
    token = create_session()
    claims = session_claims(token, settings)
    if claims is None:
        raise HTTPException(status_code=503, detail="Operator session could not be created.")
    username, issued_at, session_id = claims
    db.add(OperatorSession(
        id=session_id, username=username, issued_at=issued_at,
        expires_at=issued_at + settings.session_max_age_seconds,
    ))
    db.commit()
    response.set_cookie(
        COOKIE_NAME, token, max_age=settings.session_max_age_seconds, path="/",
        httponly=True, secure=settings.app_env == "production", samesite="lax",
    )
    return {"authenticated": True, "username": settings.operator_username}


@app.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    claims = session_claims(request.cookies.get(COOKIE_NAME, ""), settings)
    if claims:
        _, _, session_id = claims
        operator_session = db.get(OperatorSession, session_id)
        if operator_session and operator_session.revoked_at is None:
            operator_session.revoked_at = int(datetime.now(timezone.utc).timestamp())
            db.commit()
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=settings.app_env == "production", samesite="lax")
    return {"authenticated": False}


def _age_bucket(invoice: Invoice, today: date) -> str | None:
    if not _has_open_balance(invoice):
        return None
    days_late = (today - invoice.due_date).days
    if days_late <= 0:
        return "not_due"
    if days_late <= 30:
        return "late_1_30"
    if days_late <= 60:
        return "late_31_60"
    if days_late < 90:
        return "late_61_89"
    return "late_90_plus"


def _sort_invoice_items(items: list[dict], sort: str, direction: str) -> None:
    reverse = direction.casefold() == "desc"
    action_order = {"friendly": 0, "firm": 1, "offer_partial_payment": 2, "personal_call": 3,
                    "review_for_writeoff_or_collections": 4, "wait": 5}

    def key(item: dict):
        decision = item["decision"]
        if sort == "action":
            rank = decision.get("daily_rank")
            return (rank is None, rank or 10_000, action_order.get(decision["action"], 5),
                    -decision.get("expected_cash_gain", 0), item["due_date"], item["invoice_number"])
        if sort == "expected_cash":
            return (decision.get("expected_cash_gain", 0), item["due_date"], item["invoice_number"])
        if sort == "predicted":
            return (((item.get("forecast") or {}).get("quantiles", {}).get("p50") or {}).get("date", "9999-12-31"), item["due_date"])
        if sort == "drift":
            return (item["drift"].get("signal_type", ""), item["due_date"])
        if sort == "p14":
            return ((item.get("forecast") or {}).get("probabilities", {}).get("14d", -1), item["due_date"])
        if sort == "client":
            return (item["client_name"].casefold(), item["due_date"])
        if sort == "balance":
            return (float(item["outstanding"]), item["due_date"])
        if sort == "invoice":
            return item["invoice_number"].casefold()
        if sort == "status":
            return (item["status"], item["due_date"])
        return (item["due_date"], item["invoice_number"])

    items.sort(key=key, reverse=reverse if sort != "action" else False)


@app.get("/portfolio")
@app.get("/invoices")
def invoices(
    request: Request,
    q: str | None = None,
    limit: int | None = None,
    page: int = 1,
    page_size: int = Query(25, ge=1, le=100),
    statuses: str | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    predicted_week: date | None = None,
    view: str = "open",
    client_id: str | None = None,
    age_bucket: str | None = None,
    sort: str = "due",
    direction: str = "asc",
    action_only: bool = False,
    drifting_only: bool = False,
    as_of: date | None = None,
    db: Session = Depends(get_db),
) -> dict:
    user = session_user(request, db=db)
    page = max(1, page)
    if limit is not None and page_size == 25:
        page_size = limit
    if not 1 <= page_size <= 100:
        raise HTTPException(status_code=422, detail="page_size must be between 1 and 100.")
    if due_from and due_to and due_from > due_to:
        raise HTTPException(status_code=422, detail="Due date range is reversed.")
    allowed_views = {"open", "due_this_week", "overdue", "paid", "all"}
    view = view if view in allowed_views else "open"
    allowed_buckets = {"not_due", "late_1_30", "late_31_60", "late_61_89", "late_90_plus"}
    age_bucket = age_bucket if age_bucket in allowed_buckets else None
    allowed_sorts = {"action", "expected_cash", "p14", "due", "client", "balance", "invoice", "status", "predicted", "drift"}
    sort = sort if sort in allowed_sorts else "action"
    direction = "desc" if direction.casefold() == "desc" else "asc"
    public_source = _public_demo_source()
    as_of_max = _demo_as_of_max(db, public_source)
    if as_of is not None and (as_of > as_of_max or as_of < as_of_max - timedelta(days=60)):
        raise HTTPException(status_code=422, detail="Demo time travel is limited to the latest 60 days.")

    source_names = [public_source] if not user else ["demo", "paypal"]
    source_query = select(Invoice).options(joinedload(Invoice.client)).where(Invoice.source.in_(source_names))
    open_query = source_query.where(Invoice.status.not_in(CLOSED_STATUSES), Invoice.outstanding > 0)
    sql_page = (view in {"all", "paid"} and sort in {"due", "client", "balance", "invoice", "status"}
                and not any((age_bucket, drifting_only, action_only, predicted_week, db.info.get("export_all"))))
    sql_page_ids: set[str] = set()
    if sql_page:
        page_query = source_query.join(Client)
        if view == "paid":
            page_query = page_query.where(Invoice.status == "PAID")
        if client_id:
            page_query = page_query.where(Invoice.client_id == client_id)
        if statuses:
            page_query = page_query.where(Invoice.status.in_({value.strip().upper() for value in statuses.split(",") if value.strip()}))
        if due_from:
            page_query = page_query.where(Invoice.due_date >= due_from)
        if due_to:
            page_query = page_query.where(Invoice.due_date <= due_to)
        if q:
            page_query = page_query.where(or_(*[func.lower(column).contains(q.casefold(), autoescape=True)
                for column in (Invoice.invoice_number, Client.display_name, Invoice.status, Client.email)]))
        balance = case((Invoice.status.in_(CLOSED_STATUSES), 0), else_=Invoice.outstanding)
        order = {"due": [Invoice.due_date, Invoice.invoice_number],
                 "client": [func.lower(Client.display_name), Invoice.due_date],
                 "balance": [balance, Invoice.due_date], "invoice": [func.lower(Invoice.invoice_number)],
                 "status": [Invoice.status, Invoice.due_date]}[sort]
        order = [column.desc() if direction == "desc" else column.asc() for column in order]
        page_rows = list(db.scalars(page_query.order_by(*order, Invoice.invoice_number.asc(), Invoice.id.asc()).offset((page-1)*page_size).limit(page_size)).unique())
        sql_page_ids = {row.id for row in page_rows}
        # Keep the open book for portfolio aggregates and global policy ranking;
        # historical rows are hydrated only for this page.
        open_rows = list(db.scalars(open_query).unique())
        portfolio_rows = list({row.id: row for row in [*open_rows, *page_rows]}.values())
    else:
        query = source_query if view in {"all", "paid"} else open_query
        portfolio_rows = list(db.scalars(query.order_by(Invoice.due_date.asc(), Invoice.invoice_number.asc())).unique())
    _prepare_portfolio_context(db, portfolio_rows)
    base_rows = portfolio_rows
    if statuses:
        selected_statuses = {value.strip().upper() for value in statuses.split(",") if value.strip()}
        base_rows = [row for row in base_rows if row.status in selected_statuses]
    if due_from:
        base_rows = [row for row in base_rows if row.due_date >= due_from]
    if due_to:
        base_rows = [row for row in base_rows if row.due_date <= due_to]
    if client_id:
        base_rows = [row for row in base_rows if row.client_id == client_id]
    if q:
        needle = q.casefold()
        base_rows = [row for row in base_rows if any(needle in value.casefold() for value in (
            row.invoice_number, row.client.display_name, row.status, row.client.email or "",
        ))]

    activity_by_source = {source: _activity_for_source(db, source) for source in source_names}
    forecast_cache: dict[str, dict] = {}
    candidate_order = _rank_policy_candidates(db, None if user else public_source, forecast_cache)
    daily_candidate_ids = set(candidate_order[:DAILY_SUGGESTION_CAP])
    candidate_ranks = {invoice_id: index for index, invoice_id in enumerate(candidate_order[:DAILY_SUGGESTION_CAP], start=1)}

    clients_in_scope = list(db.scalars(select(Client).where(Client.source.in_(source_names))).all())
    drift_cache: dict[str, dict] = {}
    for client in clients_in_scope:
        drift_cache[client.id] = _drift_for(db, client, activity_by_source.get(client.source, {}).get(client.id))
    client_signal_types = ("paying_later", "later_than_usual", "gone_quiet")
    drifting_ids = {client_id for client_id, drift in drift_cache.items()
                    if drift.get("signal_type") in client_signal_types}
    drift_signal_counts = {
        signal: sum(client_id in drifting_ids and drift.get("signal_type") == signal
                    for client_id, drift in drift_cache.items())
        for signal in client_signal_types
    }

    ready_candidate_ids = set(_pending_policy_candidate_ids(db, candidate_order, source_names)[:DAILY_SUGGESTION_CAP])
    open_condition = and_(Invoice.status.not_in(CLOSED_STATUSES), Invoice.outstanding > 0)
    due_week_condition = or_(*[and_(Invoice.source == source, Invoice.due_date >= _today(db, source),
                                  Invoice.due_date <= _today(db, source) + timedelta(days=7)) for source in source_names])
    overdue_condition = or_(*[and_(Invoice.source == source, Invoice.due_date < _today(db, source)) for source in source_names])
    count_query = select(func.count(Invoice.id),
        func.sum(case((open_condition, 1), else_=0)),
        func.sum(case((and_(open_condition, due_week_condition), 1), else_=0)),
        func.sum(case((and_(open_condition, overdue_condition), 1), else_=0)),
        func.sum(case((Invoice.status == "PAID", 1), else_=0))).join(Client).where(Invoice.source.in_(source_names))
    if client_id:
        count_query = count_query.where(Invoice.client_id == client_id)
    if statuses:
        count_query = count_query.where(Invoice.status.in_(selected_statuses))
    if due_from:
        count_query = count_query.where(Invoice.due_date >= due_from)
    if due_to:
        count_query = count_query.where(Invoice.due_date <= due_to)
    if q:
        needle = q.casefold()
        count_query = count_query.where(or_(*[func.lower(column).contains(needle, autoescape=True)
                                             for column in (Invoice.invoice_number, Client.display_name, Invoice.status, Client.email)]))
    counts = db.execute(count_query).one()
    view_counts = {name: int(value or 0) for name, value in zip(("all", "open", "due_this_week", "overdue", "paid"), counts, strict=True)}

    if view == "open":
        rows = [row for row in base_rows if _has_open_balance(row)]
    elif view == "due_this_week":
        rows = [row for row in base_rows if _has_open_balance(row) and 0 <= (row.due_date - _today(db, row.source)).days <= 7]
    elif view == "overdue":
        rows = [row for row in base_rows if _has_open_balance(row) and row.due_date < _today(db, row.source)]
    elif view == "paid":
        rows = [row for row in base_rows if row.status == "PAID"]
    else:
        rows = base_rows
    if age_bucket:
        rows = [row for row in rows if _age_bucket(row, _today(db, row.source)) == age_bucket]
    if drifting_only:
        rows = [row for row in rows if row.client_id in drifting_ids]

    if sql_page:
        rows = [row for row in rows if row.id in sql_page_ids]
    items = []
    for row in rows:
        if row.status in CLOSED_STATUSES or row.outstanding <= 0:
            items.append({"id": row.id, "invoice_number": row.invoice_number, "client_id":row.client_id,
                          "client_name":row.client.display_name, "due_date":row.due_date.isoformat(),
                          "days_to_due":(row.due_date-_today(db,row.source)).days,
                          "outstanding":"0.00", "currency":row.currency, "status":row.status, "source":row.source,
                          "disputed":row.disputed,
                          "forecast":None, "drift":drift_cache[row.client_id],
                          "decision":{"action":"wait", "expected_cash_gain":0, "expected_cash_gain_currency":row.currency}})
        else:
            items.append(_invoice_dict(db, row, drift_cache=drift_cache,
                activity_scores=activity_by_source.get(row.source), daily_candidate_ids=daily_candidate_ids,
                forecast_cache=forecast_cache))
    for item in items:
        if item["id"] in candidate_ranks:
            item["decision"]["daily_rank"] = candidate_ranks[item["id"]]
    if predicted_week:
        week_start = predicted_week.isoformat()
        contributors = []
        for item in items:
            forecast = item.get("forecast") or {}
            weekly_probability = next((
                Decimal(str(point["probability"]))
                for point in forecast.get("weekly_settlement_probabilities", [])
                if point.get("week_start") == week_start
            ), Decimal("0"))
            contribution = Decimal(item["outstanding"]) * weekly_probability
            if contribution <= 0:
                continue
            item["cash_week_contribution"] = {
                "amount": str(contribution), "probability": str(weekly_probability),
            }
            contributors.append(item)
        items = contributors
    if action_only:
        items = [item for item in items if item["id"] in ready_candidate_ids]
    _sort_invoice_items(items, sort, direction)

    totals: dict[str, Decimal] = {}
    for row in base_rows:
        if _has_open_balance(row):
            totals[row.currency] = totals.get(row.currency, Decimal("0")) + row.outstanding
    def build_aggregates():
        aging = {name: {"count": 0, "amounts": {}} for name in allowed_buckets}
        cash_horizon = {
            name: {"count": 0, "amounts": {}}
            for name in ("next_8_weeks", "after_8_weeks", "beyond_180_days", "disputed_unforecastable")
        }
        for row in base_rows:
            bucket = _age_bucket(row, _today(db, row.source))
            if not bucket:
                continue
            aging[bucket]["count"] += 1
            amounts = aging[bucket]["amounts"]
            amounts[row.currency] = str(Decimal(amounts.get(row.currency, "0")) + Decimal(row.outstanding))

        expected_cash: dict[tuple[str, str], dict] = {}
        late_client_counts: dict[str, int] = {}
        late_rows = []
        for row in base_rows:
            if not _has_open_balance(row):
                continue
            today = _today(db, row.source)
            forecast = None
            if _payable(row):
                forecast = forecast_cache.get(row.id) or forecast_invoice(db, row, today=today, persist=True, detail=False)
                forecast_cache[row.id] = forecast
                if row.due_date < today:
                    late_client_counts[row.client_id] = late_client_counts.get(row.client_id, 0) + 1
                    late_rows.append(row)
                # Expected cash uses the survival curve's event mass, not the
                # full balance on a single median date. The same weekly mass is
                # consumed by the brief and natural-language answers.
                for point in forecast.get("weekly_settlement_probabilities", []):
                    week_start = date.fromisoformat(point["week_start"])
                    if week_start > today + timedelta(days=56):
                        continue
                    key = (week_start.isoformat(), row.currency)
                    entry = expected_cash.setdefault(key, {
                        "week_start": key[0], "currency": row.currency,
                        "amount": Decimal("0"), "invoice_count": 0,
                    })
                    probability = Decimal(str(point["probability"]))
                    if probability > 0:
                        entry["amount"] += Decimal(row.outstanding) * probability
                        entry["invoice_count"] += 1
                p50 = (forecast.get("quantiles") or {}).get("p50")
                pay_date = date.fromisoformat(p50["date"]) if p50 and p50.get("date") else None
                if pay_date is None or pay_date > today + timedelta(days=180):
                    horizon_key = "beyond_180_days"
                elif pay_date > today + timedelta(days=56):
                    horizon_key = "after_8_weeks"
                else:
                    horizon_key = "next_8_weeks"
            else:
                # Disputes and records with no usable forecast stay visible in
                # the reconciliation rather than disappearing from the cash book.
                horizon_key = "disputed_unforecastable"
            bucket = cash_horizon[horizon_key]
            bucket["count"] += 1
            bucket["amounts"][row.currency] = str(
                Decimal(bucket["amounts"].get(row.currency, "0")) + Decimal(row.outstanding)
            )

        clients_by_id = {client.id: client for client in clients_in_scope}
        top_clients = sorted(
            late_client_counts,
            key=lambda client_id: (-late_client_counts[client_id], clients_by_id[client_id].display_name.casefold()),
        )[:5]
        selected = set(top_clients)
        scenario_cash: dict[tuple[str, str], dict] = {}
        scenario_baseline: dict[str, Decimal] = {}
        scenario_invoice_count = 0
        for row in late_rows:
            if row.client_id not in selected:
                continue
            scenario_invoice_count += 1
            today = _today(db, row.source)
            forecast = forecast_cache[row.id]
            p50 = (forecast.get("quantiles") or {}).get("p50") or {}
            predicted = date.fromisoformat(p50["date"]) if p50.get("date") else today + timedelta(days=14)
            assumed_date = min(today + timedelta(days=13), max(today, predicted))
            week_start = assumed_date - timedelta(days=assumed_date.weekday())
            key = (week_start.isoformat(), row.currency)
            entry = scenario_cash.setdefault(key, {
                "week_start": key[0], "currency": row.currency, "amount": Decimal("0"),
                "invoice_count": 0, "client_contributions": {},
            })
            entry["amount"] += Decimal(row.outstanding)
            p14 = Decimal(str((forecast.get("probabilities") or {}).get("14d", 0)))
            scenario_baseline[row.currency] = scenario_baseline.get(row.currency, Decimal("0")) + Decimal(row.outstanding) * p14
            entry["invoice_count"] += 1
            client_contribution = entry["client_contributions"].setdefault(row.client_id, {
                "client_id": row.client_id, "client_name": row.client.display_name,
                "invoice_count": 0, "assumed_gross": Decimal("0"),
            })
            client_contribution["invoice_count"] += 1
            client_contribution["assumed_gross"] += Decimal(row.outstanding)
        top_late_scenario = {
            "assumption": "All currently overdue invoices for the five clients with the most late invoices settle within the next 14 days; dates are illustrative, not a prediction or payment promise.",
            "selection_rule": "Rank clients by count of currently overdue open invoices (highest first); ties are ordered by client name A–Z.",
            "client_count": len(top_clients),
            "baseline_by_currency": [{"currency": currency, "amount": str(amount)}
                                     for currency, amount in sorted(scenario_baseline.items())],
            "clients": [{"id": client_id, "name": clients_by_id[client_id].display_name,
                         "late_invoice_count": late_client_counts[client_id]} for client_id in top_clients],
            "invoice_count": scenario_invoice_count,
            "cash_by_week": [{
                **entry,
                "amount": str(entry["amount"]),
                "client_contributions": [{
                    **client,
                    "assumed_gross": str(client["assumed_gross"]),
                } for client in sorted(entry["client_contributions"].values(), key=lambda item: item["client_name"].casefold())],
            } for key, entry in sorted(scenario_cash.items())],
        }
        return aging, expected_cash, cash_horizon, top_late_scenario

    aggregate_key = (tuple(source_names), tuple((source, _today(db, source)) for source in source_names),
                     view, q, client_id, statuses, due_from, due_to)
    aging, expected_cash, cash_horizon, top_late_scenario = cached_aggregate(aggregate_key, build_aggregates)

    filtered_count = view_counts[view] if sql_page else len(items)
    page_count = max(1, (filtered_count + page_size - 1) // page_size)
    page_items = items if sql_page or db.info.get("export_all") else items[(page - 1) * page_size:page * page_size]
    model_names = sorted({item["forecast"]["model"] for item in page_items if item["forecast"]})
    ready_for_review_count = len(ready_candidate_ids)
    slim_items = []
    for item in page_items:
        slim = {key: item[key] for key in ("id", "invoice_number", "client_id", "client_name", "due_date", "days_to_due", "outstanding", "currency", "status", "source", "disputed")}
        if "cash_week_contribution" in item:
            slim["cash_week_contribution"] = item["cash_week_contribution"]
        slim["drift"] = {"signal": item["drift"].get("signal"), "signal_type":item["drift"].get("signal_type")}
        slim["decision"] = {key: item["decision"].get(key) for key in ("action", "expected_cash_gain", "expected_cash_gain_currency", "daily_rank")}
        forecast = item["forecast"]
        slim["forecast"] = {key: forecast.get(key) for key in ("quantiles", "model", "limited_history", "display_label", "scored_at")} if forecast else None
        if forecast:
            slim["forecast"]["probabilities"] = {"14d": forecast.get("probabilities", {}).get("14d")}
        slim_items.append(slim)
    page_items = slim_items
    db.commit()
    return {
        "items": page_items, "count": filtered_count, "page": page, "page_size": page_size,
        "page_count": page_count, "view": view, "view_counts": view_counts,
        "currency_totals": {key: str(value) for key, value in sorted(totals.items())},
        "aging_buckets": aging,
        "expected_cash_by_week": [{**entry, "amount": str(entry["amount"])} for key, entry in sorted(expected_cash.items())],
        "cash_horizon": cash_horizon,
        "open_balance_reconciliation": {
            "count": sum(bucket["count"] for bucket in cash_horizon.values()),
            "amounts": {
                currency: str(sum((Decimal(bucket["amounts"].get(currency, "0")) for bucket in cash_horizon.values()), Decimal("0")))
                for currency in sorted(totals)
            },
        },
        "top_late_scenario": top_late_scenario,
        "models": model_names, "synthetic_demo": not user,
        "ready_for_review_count": ready_for_review_count,
        "daily_suggestion_cap": DAILY_SUGGESTION_CAP,
        "drifting_client_count": len(drifting_ids),
        "habitually_late_client_count": sum(drift.get("signal_type") == "habitually_late" for drift in drift_cache.values()),
        "drifting_signal_counts": drift_signal_counts,
        "client_signals": sorted([{"id": client.id, "name":client.display_name, "drift":drift_cache[client.id]}
                                  for client in clients_in_scope if client.id in drifting_ids],
                                 key=lambda row: -row["drift"].get("severity_score", 0))[:5],
        "as_of": _today(db, public_source).isoformat(), "as_of_max": as_of_max.isoformat(),
        "model_timestamp": max((str(item["forecast"]["scored_at"]) for item in page_items if item.get("forecast") and item["forecast"].get("scored_at")), default=None),
    }


@app.get("/invoices/{invoice_id}")
def invoice_detail(invoice_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    _authorize_invoice(request, invoice, db)
    activity_scores = _activity_for_source(db, invoice.source)
    user = session_user(request, db=db)
    daily_candidate_ids = _top_policy_candidates(db, None if user else _public_demo_source())
    payload = _invoice_dict(db, invoice, include_detail=True, activity_scores=activity_scores,
                            daily_candidate_ids=daily_candidate_ids)
    _record_policy_decision(db, invoice, payload)
    db.commit()
    return payload


@app.get("/action-queue")
def action_queue(request: Request, db: Session = Depends(get_db)) -> dict:
    user = session_user(request, db=db)
    scope = None if user else _public_demo_source()
    query = select(Invoice).options(joinedload(Invoice.client)).where(Invoice.status.not_in(CLOSED_STATUSES), Invoice.outstanding > 0)
    if scope:
        query = query.where(Invoice.source == scope)
    rows = list(db.scalars(query).all())
    _prepare_portfolio_context(db, rows)
    forecast_cache: dict[str, dict] = {}
    sources = sorted({row.source for row in rows})
    ranked = _pending_policy_candidate_ids(db, _rank_policy_candidates(db, scope, forecast_cache), sources)
    ranked = ranked[:DAILY_SUGGESTION_CAP]
    candidate_ids = set(ranked)
    items = []
    rows_by_id = {row.id: row for row in rows}
    for invoice_id in ranked[:DAILY_SUGGESTION_CAP]:
        invoice = rows_by_id.get(invoice_id)
        if invoice is None:
            continue
        item = _invoice_dict(db, invoice, include_detail=True, include_timeline=False, daily_candidate_ids=candidate_ids,
                             forecast_cache=forecast_cache)
        if item.get("recommendation") and item["recommendation"].get("state") == "proposed":
            _record_policy_decision(db, invoice, item)
            queue_item = {key: item[key] for key in (
                "id", "invoice_number", "client_name", "client_id", "source", "currency",
                "outstanding", "due_date", "decision", "recommendation", "why", "reminder_outcomes",
            )}
            queue_item["client_email"] = item.get("client_email") if user and invoice.source == "paypal" else None
            items.append(queue_item)
    collections_review = []
    collections_closed = []
    invoice_ids = [invoice.id for invoice in rows]
    reviews_by_invoice = {
        review.invoice_id: review for review in db.scalars(
            select(CollectionReview).where(CollectionReview.invoice_id.in_(invoice_ids))
        ).all()
    } if invoice_ids else {}
    for invoice in rows:
        days_late = (_today(db, invoice.source) - invoice.due_date).days
        if not _payable(invoice) or days_late < 90:
            continue
        review = reviews_by_invoice.get(invoice.id)
        summary = _invoice_dict(
            db, invoice, include_detail=False, include_timeline=False,
            forecast_cache=forecast_cache, create_recommendation=False,
        )
        if summary["decision"]["action"] != "review_for_writeoff_or_collections":
            continue
        review_item = {
            "id": invoice.id, "invoice_number": invoice.invoice_number,
            "client_name": invoice.client.display_name, "client_id": invoice.client_id,
            "source": invoice.source, "currency": invoice.currency,
            "outstanding": str(invoice.outstanding), "due_date": invoice.due_date.isoformat(),
            "days_late": days_late, "decision": summary["decision"],
            "recommendation": None,
            "why": summary["why"],
            "suggested_review_steps": ["call", "send_final_notice", "mark_disputed", "write_off"],
            "collection_review": {
                "owner": review.owner if review else "",
                "notes": review.notes if review else "",
                "follow_up_date": review.follow_up_date.isoformat() if review and review.follow_up_date else None,
                "resolution_status": review.resolution_status if review else "open",
                "updated_at": review.updated_at.isoformat() if review else None,
            },
        }
        if review and review.resolution_status in {"resolved", "written_off"}:
            collections_closed.append(review_item)
        else:
            collections_review.append(review_item)
    collections_review.sort(key=lambda row: (row["currency"], -row["days_late"], row["due_date"], row["invoice_number"]))
    collections_closed.sort(key=lambda row: ((row["collection_review"].get("updated_at") or ""), row["invoice_number"]), reverse=True)
    collections_totals: dict[str, Decimal] = {}
    for row in collections_review:
        collections_totals[row["currency"]] = collections_totals.get(row["currency"], Decimal("0")) + Decimal(row["outstanding"])
    db.commit()
    skipped = list(db.scalars(select(Recommendation).options(
        joinedload(Recommendation.invoice).joinedload(Invoice.client),
    ).join(Invoice).where(
        Recommendation.state == "skipped", Invoice.source == scope if scope else text("1=1"),
        or_(*[and_(Invoice.source == source, Recommendation.policy_date == _today(db, source)) for source in ([scope] if scope else ["demo", "paypal"])]),
    )).all())
    replacement_by_invoice = {}
    if skipped:
        replacements = db.scalars(select(Recommendation).where(
            Recommendation.invoice_id.in_([row.invoice_id for row in skipped]),
            Recommendation.state.in_(["proposed", "sending", "sent", "applied"]),
        ).order_by(Recommendation.created_at.desc())).all()
        for replacement in replacements:
            replacement_by_invoice.setdefault(replacement.invoice_id, replacement)
    metric_rows = list(db.scalars(select(AIAudit).where(AIAudit.kind == "review_time").order_by(AIAudit.created_at.desc())).all())
    measured = [int(row.output["review_seconds"]) for row in metric_rows
                if row.facts.get("source") == (scope or "paypal") and row.output.get("review_seconds")][:200]
    return {"items": items, "count": len(items), "pending_count": len(items), "skipped_count": len(skipped),
            "skipped": [{"id": row.id, "invoice_id": row.invoice_id,
                         "invoice_number": row.invoice.invoice_number,
                         "client_name": row.invoice.client.display_name,
                         "replacement_state": replacement_by_invoice[row.invoice_id].state if row.invoice_id in replacement_by_invoice else None,
                         "replacement_id": replacement_by_invoice[row.invoice_id].id if row.invoice_id in replacement_by_invoice else None} for row in skipped], "daily_cap": DAILY_SUGGESTION_CAP,
            "review_time": {"completed_count": len(measured), "median_seconds": statistics.median(measured) if measured else None,
                            "sample_window": "latest 200 completed queue actions"},
            "collections_review": collections_review, "collections_closed": collections_closed,
            "collections_totals": {key: str(value) for key, value in sorted(collections_totals.items())},
            "as_of": _today(db, scope).isoformat() if scope else _today(db).isoformat(),
            "synthetic_demo": _is_demo_source(scope)}


@app.post("/invoices/{invoice_id}/draft")
def draft_invoice_reminder(invoice_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    operator = _authorize_invoice(request, invoice, db)
    if invoice.source == "paypal" and not operator:
        raise HTTPException(status_code=401, detail="Operator login required to draft PayPal reminders.")
    activity = _activity_for_source(db, invoice.source)
    summary = _invoice_dict(db, invoice, include_detail=True, activity_scores=activity,
                            daily_candidate_ids=_top_policy_candidates(db, invoice.source))
    if summary["decision"]["action"] not in {"friendly", "firm"} or not summary["recommendation"]:
        raise HTTPException(status_code=409, detail="There is no pending reminder recommendation to draft.")
    recommendation = db.get(Recommendation, summary["recommendation"]["id"])
    if not recommendation or recommendation.state != "proposed" or recommendation.invoice_version != invoice.material_version:
        raise HTTPException(status_code=409, detail="The reminder recommendation is no longer editable.")
    provider_url = summary.get("payment_url")
    parsed_url = urlparse(provider_url) if isinstance(provider_url, str) else None
    if not parsed_url or parsed_url.scheme != "https" or not parsed_url.hostname or not parsed_url.hostname.endswith("paypal.com"):
        provider_url = None
    fallback = render_message(invoice.client.display_name, invoice.outstanding, invoice.currency, invoice.due_date,
                              recommendation.tone, provider_url)
    facts = _draft_facts(invoice, invoice.client, summary.get("forecast"), summary.get("drift", {}),
                         summary["decision"], provider_url, _client_history_facts(db, invoice.client_id))
    from app.services.ai import GEMINI_MODEL
    body, source = generate_reminder_draft(facts, fallback)
    recommendation.draft_text = body
    recommendation.draft_source = source
    recommendation.draft_model = GEMINI_MODEL if source == "gemini" else None
    recommendation.drafted_at = datetime.now(timezone.utc)
    recommendation.edited_text = body
    db.add(AIAudit(kind="draft", invoice_id=invoice.id, facts=facts,
                   output={"text": body, "source": source, "model": recommendation.draft_model}))
    db.commit()
    return {
        "recommendation": _recommendation_dict(recommendation),
        "source": source,
        "provider": "Google Gemini" if source == "gemini" else "grounded template",
        "model": GEMINI_MODEL if source == "gemini" else None,
        "grounded_in": f"Invoice {invoice.invoice_number}, {invoice.currency} {invoice.outstanding}, due {invoice.due_date.isoformat()}, reminder history and client settlement facts",
        "note": "Google Gemini generated this draft from the listed invoice and client-history facts. Review and edit it before approval." if source == "gemini" else "Gemini was unavailable or its draft failed validation; the grounded template is shown instead.",
    }


@app.post("/invoices/{invoice_id}/reply")
def triage_invoice_reply(invoice_id: str, payload: ReplyInput, request: Request, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    operator = _authorize_invoice(request, invoice, db)
    reply = payload.reply.strip()
    if not reply:
        raise HTTPException(status_code=422, detail="Paste the client's reply first.")
    if invoice.source == "paypal" and not operator:
        raise HTTPException(status_code=401, detail="Operator login required to record PayPal client replies.")
    classification, classifier = classify_reply(reply)
    promised_date = extract_promised_date(reply, _today(db, invoice.source)) if classification == "promise_to_pay" else None
    reviewed_at = datetime.now(timezone.utc)
    previous_pending = db.scalars(select(ReplyTriage).where(
        ReplyTriage.invoice_id == invoice.id, ReplyTriage.state == "pending",
    )).all()
    for previous in previous_pending:
        previous.state = "superseded"
        previous.reviewed_at = reviewed_at
        db.add(AIAudit(kind="reply_triage", invoice_id=invoice.id,
                       facts={"actor": operator or "synthetic demo operator", "channel": "operator web UI", "reply_text": previous.reply_text},
                       output={"state": "superseded", "classification": previous.classification,
                               "promised_payment_date": previous.promised_payment_date.isoformat() if previous.promised_payment_date else None}))
    triage = ReplyTriage(
        invoice_id=invoice.id, reply_text=reply, classification=classification,
        promised_payment_date=promised_date, classifier=classifier, state="pending",
    )
    db.add(triage)
    db.add(AIAudit(kind="reply_triage", invoice_id=invoice.id,
                   facts={"actor": operator or "synthetic demo operator", "channel": "operator web UI", "reply_text": reply},
                   output={"state": "pending", "classification": classification,
                           "promised_payment_date": promised_date.isoformat() if promised_date else None}))
    db.commit()
    db.refresh(triage)
    return {
        "id": triage.id,
        "reply_text": triage.reply_text,
        "classification": classification,
        "classifier": classifier,
        "promised_payment_date": promised_date.isoformat() if promised_date else None,
        "state": triage.state,
        "reminders_paused": bool(invoice.disputed or (invoice.promised_payment_date and _today(db, invoice.source) <= invoice.promised_payment_date)),
        "note": "Preview only. Review or correct the extracted type and date, then approve it to update invoice state.",
    }


@app.post("/invoices/{invoice_id}/reply-triage/{triage_id}/apply")
def apply_reply_triage(invoice_id: str, triage_id: str, payload: ReplyReviewInput, request: Request,
                       db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    _authorize_invoice(request, invoice, db)
    triage = db.get(ReplyTriage, triage_id)
    if not triage or triage.invoice_id != invoice.id:
        raise HTTPException(status_code=404, detail="Reply preview not found.")
    if triage.state != "pending":
        raise HTTPException(status_code=409, detail="This reply preview has already been reviewed.")
    promised_date = (
        payload.promised_payment_date
        if "promised_payment_date" in payload.model_fields_set
        else triage.promised_payment_date
    )
    if payload.classification == "promise_to_pay" and promised_date is None:
        raise HTTPException(status_code=422, detail="Correct the promised payment date before approving this reply.")
    if payload.classification == "promise_to_pay" and promised_date < _today(db, invoice.source):
        raise HTTPException(status_code=422, detail="The promised payment date is in the past. Correct it before approving.")
    triage.classification = payload.classification
    triage.promised_payment_date = promised_date if payload.classification == "promise_to_pay" else None
    triage.state = "applied"
    triage.reviewed_at = datetime.now(timezone.utc)
    if payload.classification == "dispute":
        invoice.disputed = True
        invoice.promised_payment_date = None
        invoice.material_version += 1
    elif payload.classification == "promise_to_pay" and invoice.promised_payment_date != promised_date:
        invoice.promised_payment_date = promised_date
        invoice.material_version += 1
    db.add(AIAudit(kind="reply_triage", invoice_id=invoice.id,
                   facts={"actor": session_user(request, db=db) or "synthetic demo operator", "channel": "operator web UI", "reply_text": triage.reply_text},
                   output={"state": "applied", "classification": triage.classification,
                           "promised_payment_date": triage.promised_payment_date.isoformat() if triage.promised_payment_date else None}))
    db.commit()
    return {"id": triage.id, "state": triage.state, "classification": triage.classification,
            "promised_payment_date": triage.promised_payment_date.isoformat() if triage.promised_payment_date else None,
            "disputed": invoice.disputed,
            "note": "Reply review applied. The invoice and reminder policy now reflect the approved classification."}


@app.get("/invoices/{invoice_id}/reply-triage/{triage_id}")
def reply_triage_status(invoice_id: str, triage_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    _authorize_invoice(request, invoice, db)
    triage = db.get(ReplyTriage, triage_id)
    if not triage or triage.invoice_id != invoice.id:
        raise HTTPException(status_code=404, detail="Reply preview not found.")
    return {
        "id": triage.id, "state": triage.state, "classification": triage.classification,
        "promised_payment_date": triage.promised_payment_date.isoformat() if triage.promised_payment_date else None,
        "reviewed_at": triage.reviewed_at.isoformat() if triage.reviewed_at else None,
        "disputed": invoice.disputed,
        "invoice_promised_payment_date": invoice.promised_payment_date.isoformat() if invoice.promised_payment_date else None,
    }


@app.post("/invoices/{invoice_id}/reply-triage/{triage_id}/dismiss")
def dismiss_reply_triage(invoice_id: str, triage_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    _authorize_invoice(request, invoice, db)
    triage = db.get(ReplyTriage, triage_id)
    if not triage or triage.invoice_id != invoice.id:
        raise HTTPException(status_code=404, detail="Reply preview not found.")
    if triage.state != "pending":
        raise HTTPException(status_code=409, detail="This reply preview has already been reviewed.")
    triage.state = "dismissed"
    triage.reviewed_at = datetime.now(timezone.utc)
    db.add(AIAudit(kind="reply_triage", invoice_id=invoice.id,
                   facts={"actor": session_user(request, db=db) or "synthetic demo operator", "channel": "operator web UI", "reply_text": triage.reply_text},
                   output={"state": "dismissed", "classification": triage.classification,
                           "promised_payment_date": triage.promised_payment_date.isoformat() if triage.promised_payment_date else None}))
    db.commit()
    return {"id": triage.id, "state": triage.state, "note": "Reply preview dismissed without changing invoice state."}


@app.post("/invoices/{invoice_id}/resolve-dispute")
def resolve_invoice_dispute(invoice_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    _authorize_invoice(request, invoice, db)
    if invoice.disputed:
        invoice.disputed = False
        invoice.material_version += 1
        db.commit()
    return {"disputed": invoice.disputed,
            "note": "Dispute resolved. Review the current recommendation before sending any reminder."}


@app.patch("/invoices/{invoice_id}/collection-review")
def update_collection_review(invoice_id: str, payload: CollectionReviewInput, request: Request,
                             db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    _authorize_invoice(request, invoice, db)
    review = db.get(CollectionReview, invoice.id)
    if review is None:
        review = CollectionReview(invoice_id=invoice.id)
        db.add(review)
    before = {"owner": review.owner, "notes": review.notes,
              "follow_up_date": review.follow_up_date.isoformat() if review.follow_up_date else None,
              "resolution_status": review.resolution_status}
    review.owner = payload.owner.strip()
    review.notes = payload.notes.strip()
    review.follow_up_date = payload.follow_up_date
    review.resolution_status = payload.resolution_status
    review.updated_at = datetime.now(timezone.utc)
    actor = session_user(request, db=db) or "synthetic demo operator"
    db.add(AIAudit(kind="collection_review", invoice_id=invoice.id,
                   facts={"actor": actor, "channel": "operator web UI", "before": before},
                   output={"after": {"owner": review.owner, "notes": review.notes,
                                     "follow_up_date": review.follow_up_date.isoformat() if review.follow_up_date else None,
                                     "resolution_status": review.resolution_status}}))
    db.commit()
    return {"invoice_id": invoice.id, "owner": review.owner, "notes": review.notes,
            "follow_up_date": review.follow_up_date.isoformat() if review.follow_up_date else None,
            "resolution_status": review.resolution_status, "updated_at": review.updated_at.isoformat()}


@app.post("/invoices/{invoice_id}/review-metric")
def record_review_metric(invoice_id: str, payload: ReviewMetricInput, request: Request,
                         db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    _authorize_invoice(request, invoice, db)
    db.add(AIAudit(
        kind="review_time", invoice_id=invoice.id,
        facts={"source": invoice.source},
        output={"review_seconds": payload.review_seconds, "operation": payload.operation},
    ))
    db.commit()
    return {"recorded": True}


@app.get("/clients")
def clients(request: Request, q: str | None = None, options_only: bool = False, db: Session = Depends(get_db)) -> dict:
    user = session_user(request, db=db)
    query = select(Client).order_by(Client.display_name.asc())
    if not user:
        query = query.where(Client.source == _public_demo_source())
    if q:
        query = query.where(Client.display_name.ilike(f"%{q}%"))
    if options_only:
        return {"items": [{"id": row.id, "name": row.display_name} for row in db.scalars(query.limit(30))]}
    rows = list(db.scalars(query).all())
    source_names = {row.source for row in rows}
    counts_by_client = dict(db.execute(select(Invoice.client_id, func.count(Invoice.id)).where(Invoice.source.in_(source_names)).group_by(Invoice.client_id)).all())
    days = {_today(db, source) for source in source_names}
    saved_signals = {row.key.removeprefix("drift:"): row.payload for row in db.scalars(select(ReadModel).where(ReadModel.key.like("drift:%"), ReadModel.as_of.in_(days))).all()}
    output = []
    for client in rows:
        output.append({"id": client.id, "name": client.display_name, "source": client.source,
                       "invoice_count": counts_by_client.get(client.id, 0),
                       "drift": saved_signals.get(client.id) or _drift_for(db, client)})
    output.sort(key=lambda row: (-row["drift"].get("severity_score", 0), row["name"].casefold()))
    counts = {name: sum(row["drift"].get("signal_type") == name for row in output)
              for name in ("paying_later", "later_than_usual", "gone_quiet", "habitually_late", "insufficient_history")}
    return {"items": output, "signal_counts": counts}


@app.get("/demo-stories")
def demo_stories(request: Request, db: Session = Depends(get_db)) -> dict:
    """Return links to the synthetic narratives used by the guided demo."""
    source = _public_demo_source()
    clients = list(db.scalars(select(Client).where(Client.source == source)).all())
    by_name = {client.display_name.casefold(): client for client in clients}
    invoices = list(db.scalars(
        select(Invoice).options(joinedload(Invoice.client)).where(Invoice.source == source)
    ).all())
    today = _today(db, source)
    verdant = [row for row in invoices if row.client.display_name.casefold() == "verdant market"
               and row.outstanding > 0 and row.status not in CLOSED_STATUSES]
    verdant.sort(key=lambda row: (row.due_date, row.invoice_number))
    cinder_dispute = next((row for row in invoices if row.client.display_name.casefold() == "cinder & co" and row.disputed), None)
    harbor_firm = next((row for row in invoices if row.client.display_name.casefold() == "harborlight design"
                        and any(isinstance(event, dict) and str(event.get("tone", "")).casefold() == "firm"
                                for event in (row.reminder_events or []))), None)
    blue_cedar = by_name.get("blue cedar labs")

    def story(key: str, title: str, summary: str, invoice: Invoice | None = None, client: Client | None = None) -> dict:
        destination = f"/invoices/{invoice.id}" if invoice else f"/clients/{client.id}" if client else "/"
        return {"key": key, "title": title, "summary": summary, "href": destination}

    verdant_summary = (
        f"Oldest selected open invoice is {(today - verdant[0].due_date).days} days past due; overdue age is a lower bound while unpaid."
        if verdant else "No open Verdant invoice is available in this synthetic story."
    )
    return {"items": [
        story("verdant_outlier", "Verdant Market · stale outlier",
              verdant_summary,
              verdant[0] if verdant else None, by_name.get("verdant market")),
        story("dispute", "Cinder & Co · disputed invoice",
              "A recorded dispute pauses reminder recommendations while the balance is reviewed.", cinder_dispute,
              by_name.get("cinder & co")),
        story("firm_reminder", "Harborlight Design · firm reminder",
              "A synthetic payment settles after a recorded firm reminder; sequence does not establish causation.",
              harbor_firm, by_name.get("harborlight design")),
        story("terms_shift", "Blue Cedar Labs · terms changed",
              "The synthetic history changes from NET 30 to NET 15 so the client story can be inspected.",
              client=blue_cedar),
    ]}


@app.post("/clients/{client_id}/what-if")
def client_terms_what_if(client_id: str, payload: WhatIfInput, request: Request, db: Session = Depends(get_db)) -> dict:
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")
    if not session_user(request, db=db) and client.source != _public_demo_source():
        raise HTTPException(status_code=401, detail="Operator login required to simulate PayPal client terms.")
    records = _client_records(db, client.id)
    eligible = [row for row in records if row["status"] not in CLOSED_STATUSES and Decimal(str(row["outstanding"])) > 0]
    if not eligible:
        eligible = records
    if not eligible:
        raise HTTPException(status_code=409, detail="This client has no invoice history to build a terms scenario.")
    currencies = {}
    amounts: dict[str, list[float]] = {}
    for row in records:
        currencies[row["currency"]] = currencies.get(row["currency"], 0) + 1
        if row["status"] == "PAID":
            amounts.setdefault(row["currency"], []).append(float(row["total"]))
    currency = max(currencies, key=currencies.get)
    amount_values = amounts.get(currency) or [float(row["total"]) for row in records if row["currency"] == currency]
    amount = float(statistics.median(amount_values))
    representative = max((row for row in eligible if row["currency"] == currency), key=lambda row: row["issue_date"], default=eligible[0])
    invoice = db.get(Invoice, representative["id"])
    if invoice is None:
        raise HTTPException(status_code=404, detail="Client invoice history is unavailable.")
    as_of = _today(db, client.source)

    def score(term: str, partial_allowed: bool) -> dict:
        scenario = {
            "issue_date": as_of,
            "due_date": as_of + timedelta(days=15 if term == "NET_15" else 30),
            "total": amount, "paid": 0.0, "outstanding": amount,
            "status": "SENT", "payment_term_type": term,
            "partial_payment_allowed": partial_allowed,
            "viewed_at": None, "viewed_by_recipient": False, "viewed_observed_at": None,
            "reminder_events": [], "partial_at": None, "partial_amount": 0.0,
            "settled_at": None, "paid_at": None, "cancelled_at": None,
        }
        forecast = forecast_invoice(db, invoice, today=as_of, persist=False, scenario=scenario)
        return {"payment_term_type": term, "partial_payment_allowed": partial_allowed,
                "issue_date": scenario["issue_date"].isoformat(), "due_date": scenario["due_date"].isoformat(),
                "p50": forecast["quantiles"]["p50"], "quantiles": forecast["quantiles"],
                "coverage_interval": forecast["coverage_interval"], "probabilities": forecast["probabilities"],
                "model": forecast["model"], "limited_history": forecast["limited_history"]}

    baseline = score("NET_30", False)
    term_scenario = score(payload.payment_term_type, False)
    partial_scenario = score(payload.payment_term_type, True)
    return {"as_of": as_of.isoformat(), "client_id": client.id, "client_name": client.display_name,
            "currency": currency, "representative_amount": str(Decimal(str(amount)).quantize(Decimal("0.01"))),
            "representative_issue_date": as_of.isoformat(), "baseline_term": "NET_30",
            "baseline": baseline, "with_terms": term_scenario, "with_partial_payment": partial_scenario}


def _scoped_invoice_rows(request: Request, db: Session) -> list[Invoice]:
    query = select(Invoice).options(joinedload(Invoice.client)).order_by(Invoice.due_date.asc())
    if not session_user(request, db=db):
        query = query.where(Invoice.source == _public_demo_source())
    return list(db.scalars(query).all())


def _scoped_clients(request: Request, db: Session) -> list[Client]:
    query = select(Client).order_by(Client.display_name.asc())
    if not session_user(request, db=db):
        query = query.where(Client.source == _public_demo_source())
    return list(db.scalars(query).all())


@app.get("/facts/invoices/{invoice_id}")
def invoice_facts(invoice_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    _authorize_invoice(request, invoice, db)
    activity = _activity_for_source(db, invoice.source)
    summary = _invoice_dict(db, invoice, drift_cache={}, activity_scores=activity,
                            daily_candidate_ids=_top_policy_candidates(db, invoice.source), create_recommendation=False)
    recommendation = summary["recommendation"]
    if (not recommendation or recommendation["state"] != "proposed"
            or recommendation["invoice_version"] != invoice.material_version
            or recommendation["action"] != summary["decision"]["action"]):
        recommendation = None
    return {
        "as_of": _today(db, invoice.source).isoformat(),
        "invoice": {key: summary[key] for key in (
            "id", "invoice_number", "client_id", "client_name", "issue_date", "due_date", "days_to_due",
            "total", "paid", "outstanding", "currency", "status", "disputed", "promised_payment_date",
            "source", "viewed_at", "partial_payment_allowed", "payment_term_type", "reminder_events",
        )},
        "forecast": summary["forecast"],
        "why": summary["why"],
        "client": {"id": invoice.client.id, "name": invoice.client.display_name,
                   "history": _client_history_facts(db, invoice.client_id), "drift": summary["drift"]},
        "policy": summary["decision"],
        "recommendation": recommendation,
    }


@app.get("/facts/clients/{client_id}")
def client_facts(client_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")
    if not session_user(request, db=db) and client.source != _public_demo_source():
        raise HTTPException(status_code=401, detail="Operator login required to view PayPal client facts.")
    query = select(Invoice).options(joinedload(Invoice.client)).where(Invoice.client_id == client.id).order_by(Invoice.issue_date.desc())
    rows = list(db.scalars(query).all())
    _prepare_portfolio_context(db, _scoped_invoice_rows(request, db))
    history = _client_history_facts(db, client.id)
    history_rows = {row["id"]: row for row in _client_records(db, client.id)}
    activity = _activity_for_source(db, client.source)
    drift = _drift_for(db, client, activity.get(client.id))
    cache: dict[str, dict] = {}
    candidates = _top_policy_candidates(db, client.source)
    invoice_facts = []
    for row in rows:
        item = _invoice_dict(db, row, drift_cache={client.id: drift}, activity_scores=activity,
                             daily_candidate_ids=candidates, forecast_cache=cache, create_recommendation=False)
        values = {key: item[key] for key in (
            "id", "invoice_number", "issue_date", "due_date", "days_to_due", "total", "paid", "outstanding",
            "currency", "status", "forecast", "why", "decision",
        )}
        values["payment_term_type"] = row.payment_term_type
        values["disputed"] = row.disputed
        settled_at = history_rows[row.id].get("settled_at")
        values["settled_at"] = settled_at.isoformat() if settled_at else None
        values["days_late"] = (settled_at - row.due_date).days if settled_at else None
        invoice_facts.append(values)
    stories = _client_stories(
        list(history_rows.values()), _today(db, client.source),
        {row.id: row.invoice_number for row in rows},
    )
    return {
        "as_of": _today(db, client.source).isoformat(),
        "client": {"id": client.id, "name": client.display_name, "source": client.source},
        "history": history,
        "drift": drift,
        "stories": stories,
        "invoices": invoice_facts,
    }


@app.get("/facts/morning-brief")
def morning_brief(request: Request, db: Session = Depends(get_db)) -> dict:
    rows = _scoped_invoice_rows(request, db)
    open_rows = [row for row in rows if _payable(row)]
    user = session_user(request, db=db)
    public_source = _public_demo_source()
    candidate_ids = _top_policy_candidates(db, None if user else public_source)
    forecast_cache: dict[str, dict] = {}
    totals: dict[str, Decimal] = {}
    invoice_counts: dict[str, int] = {}
    invoice_ids_by_currency: dict[str, list[str]] = {}
    candidates = []
    today = _today(db, public_source if not user else None)
    week_end = today + timedelta(days=6 - today.weekday())
    for row in open_rows:
        item = _invoice_dict(db, row, daily_candidate_ids=candidate_ids, forecast_cache=forecast_cache,
                             create_recommendation=False)
        forecast = item.get("forecast") or {}
        row_today = _today(db, row.source)
        current_week = (row_today - timedelta(days=row_today.weekday())).isoformat()
        weekly_probability = next((
            Decimal(str(point["probability"]))
            for point in forecast.get("weekly_settlement_probabilities", [])
            if point.get("week_start") == current_week
        ), Decimal("0"))
        if weekly_probability > 0:
            totals[row.currency] = totals.get(row.currency, Decimal("0")) + Decimal(item["outstanding"]) * weekly_probability
            invoice_counts[row.currency] = invoice_counts.get(row.currency, 0) + 1
            invoice_ids_by_currency.setdefault(row.currency, []).append(row.id)
        if item["decision"]["action"] in {"friendly", "firm", "offer_partial_payment"}:
            candidates.append({
                "invoice_id": row.id, "invoice_number": row.invoice_number, "client_id": row.client_id,
                "client_name": row.client.display_name, "currency": row.currency,
                "outstanding": item["outstanding"], "action": item["decision"]["action"],
                "reason": item["decision"]["reason"], "expected_cash_gain": item["decision"]["expected_cash_gain"],
                "p14": (item.get("forecast") or {}).get("probabilities", {}).get("14d"),
            })
    candidates.sort(key=lambda item: (-float(item["expected_cash_gain"]), item["invoice_number"]))
    drifting = []
    for client in _scoped_clients(request, db):
        signal = _drift_for(db, client)
        if signal.get("signal_type") in {"paying_later", "later_than_usual", "gone_quiet"}:
            drifting.append({"client_id": client.id, "client_name": client.display_name, **signal})
    drifting.sort(key=lambda item: (-float(item.get("severity_score", 0)), item["client_name"].casefold()))
    return {
        "as_of": today.isoformat(), "week_end": week_end.isoformat(),
        "expected_cash_this_week": [
            {"currency": currency, "amount": str(amount), "invoice_count": invoice_counts[currency],
             "invoice_ids": invoice_ids_by_currency.get(currency, [])}
            for currency, amount in sorted(totals.items())
        ],
        "top_candidates": candidates[:3],
        "clients_slowing_down": drifting[:5],
    }


@app.post("/ask")
def ask_receivables(payload: AskInput, request: Request, db: Session = Depends(get_db)) -> dict:
    question = " ".join(payload.question.split())
    lowered = question.casefold()
    rows = _scoped_invoice_rows(request, db)
    user = session_user(request, db=db)
    facts = {"as_of": _today(db, _public_demo_source() if not user else None).isoformat(),
             "invoices": [{"id": row.id, "client_id": row.client_id, "client_name": row.client.display_name,
                            "status": row.status, "currency":row.currency, "outstanding":str(row.outstanding),
                            "due_date":row.due_date.isoformat()} for row in rows]}
    def grounded(value):
        value["as_of"] = facts["as_of"]
        value["grounded_in"] = f"{len(rows)} scoped invoice facts as of {facts['as_of']}; currencies are not converted"
        db.add(AIAudit(kind="answer", facts={**facts, "question":question, "result_evidence":value.get("data")}, output=value))
        db.commit()
        return value
    currency_match = re.search(r"\bin\s+(usd|eur|gbp|cad|aud|dollars?|euros?|pounds?)\b", lowered)
    currency = None
    if currency_match:
        currency = currency_match.group(1).upper()
        currency = {"DOLLAR": "USD", "DOLLARS": "USD", "EURO": "EUR", "EUROS": "EUR", "POUND": "GBP", "POUNDS": "GBP"}.get(currency, currency)

    if re.search(r"\b(most|highest|largest)\b", lowered) and re.search(r"\b(owe|owes|owed|balance|receivable)\b", lowered) and currency:
        balances: dict[str, Decimal] = {}
        counts: dict[str, int] = {}
        names: dict[str, str] = {}
        invoice_ids: dict[str, list[str]] = {}
        for row in rows:
            if row.currency == currency and _payable(row):
                balances[row.client_id] = balances.get(row.client_id, Decimal("0")) + row.outstanding
                counts[row.client_id] = counts.get(row.client_id, 0) + 1
                names[row.client_id] = row.client.display_name
                invoice_ids.setdefault(row.client_id, []).append(row.id)
        winner = max(balances, key=balances.get) if balances else None
        data = None if winner is None else {
            "client_id": winner, "client_name": names[winner], "currency": currency,
            "outstanding": str(balances[winner]), "open_invoice_count": counts[winner],
            "invoices": [{"id": row.id, "invoice_number": row.invoice_number}
                         for row in rows if row.id in set(invoice_ids[winner])],
        }
        answer = f"{data['client_name']} has the largest open {currency} balance: {currency} {Decimal(data['outstanding']):,.2f} across {data['open_invoice_count']} invoices." if data else f"There are no open {currency} balances."
        return grounded({"intent": "largest_client_balance", "answer": answer, "data": data})

    if re.search(r"\b(slow|slowing|drift|later than usual|gone quiet)\b", lowered) and re.search(r"\b(client|customer|who|which)\b", lowered):
        clients_by_id = {client.id: client for client in _scoped_clients(request, db)}
        signals = []
        for client in clients_by_id.values():
            signal = _drift_for(db, client)
            if signal.get("signal_type") in {"paying_later", "later_than_usual", "gone_quiet"}:
                signals.append({"client_id": client.id, "client_name": client.display_name,
                                "signal_type": signal["signal_type"], "message": signal["message"],
                                "severity_score": signal.get("severity_score", 0),
                                "invoices": [{"id": row.id, "invoice_number": row.invoice_number}
                                             for row in rows if row.client_id == client.id and _payable(row)]})
        signals.sort(key=lambda item: (-float(item["severity_score"]), item["client_name"].casefold()))
        names = ", ".join(item["client_name"] for item in signals[:5])
        answer = f"Clients with timing signals: {names}." if names else "No client has a current delay or cadence signal."
        return grounded({"intent": "clients_slowing_down", "answer": answer, "data": signals[:5]})

    if re.search(r"\b(cash|payments?|settlements?)\b", lowered) and re.search(r"\b(this week|next 7 days)\b", lowered):
        totals: dict[str, Decimal] = {}
        counts: dict[str, int] = {}
        invoice_ids_by_currency: dict[str, list[str]] = {}
        for row in rows:
            if not _payable(row):
                continue
            today = _today(db, row.source)
            forecast = forecast_invoice(db, row, today=today, persist=True)
            if "this week" in lowered:
                current_week = (today - timedelta(days=today.weekday())).isoformat()
                probability = next((
                    Decimal(str(point["probability"]))
                    for point in forecast.get("weekly_settlement_probabilities", [])
                    if point.get("week_start") == current_week
                ), Decimal("0"))
            else:
                probability = Decimal(str((forecast.get("probabilities") or {}).get("7d", 0)))
            if probability > 0:
                totals[row.currency] = totals.get(row.currency, Decimal("0")) + row.outstanding * probability
                counts[row.currency] = counts.get(row.currency, 0) + 1
                invoice_ids_by_currency.setdefault(row.currency, []).append(row.id)
        data = [{"currency": key, "amount": str(value), "invoice_count": counts[key],
                 "invoices": [{"id": row.id, "invoice_number": row.invoice_number}
                              for row in rows if row.id in set(invoice_ids_by_currency.get(key, []))]}
                for key, value in sorted(totals.items())]
        window = "this calendar week" if "this week" in lowered else "the next 7 days"
        answer = f"Probability-weighted expected cash for {window}: " + (", ".join(f"{item['currency']} {Decimal(item['amount']):,.2f}" for item in data) if data else "none.")
        return grounded({"intent": "expected_cash_this_week", "answer": answer, "data": data})

    if "overdue" in lowered:
        overdue = [row for row in rows if _payable(row) and row.due_date < _today(db, row.source) and (not currency or row.currency == currency)]
        overdue.sort(key=lambda row: (-row.outstanding, row.due_date))
        data = [{"invoice_id": row.id, "invoice_number": row.invoice_number, "client_name": row.client.display_name,
                 "client_id": row.client_id, "currency": row.currency, "outstanding": str(row.outstanding), "due_date": row.due_date.isoformat()}
                for row in overdue[:5]]
        answer = f"There are {len(overdue)} overdue open invoices" + (f" in {currency}" if currency else "") + "."
        return grounded({"intent": "overdue_invoices", "answer": answer, "data": data})

    return grounded({
        "intent": "unsupported", "answer": "I can answer largest open balance by currency, clients with timing signals, expected cash this week, or overdue invoices.",
        "data": None,
    })


@app.post("/sync")
def sync(request: Request, db: Session = Depends(get_db)) -> dict:
    require_operator(request, db)
    try:
        return sync_paypal(db, PayPalAdapter())
    except PayPalError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/sync/status")
def sync_status(request: Request, db: Session = Depends(get_db)) -> dict:
    require_operator(request, db)
    latest = db.scalar(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1))
    webhook_failures = db.scalar(select(func.count(InvoiceEvent.id)).where(
        InvoiceEvent.verified.is_(True), InvoiceEvent.processing_status.in_(["retry", "verification_retry"]),
    )) or 0
    recent_webhooks = list(db.scalars(select(InvoiceEvent).order_by(InvoiceEvent.received_at.desc()).limit(8)).all())
    return {
        "last_run": ({
            "started_at": latest.started_at.isoformat(), "completed_at": latest.completed_at.isoformat() if latest.completed_at else None,
            "status": latest.status, "created": latest.created_count, "updated": latest.updated_count,
            "event_refreshes": latest.event_refreshes, "retry_failures": latest.retry_failures,
            "errors": (latest.errors or [])[:5],
        } if latest else None),
        "webhook_failures": int(webhook_failures),
        "webhook_events": [{"event_type": row.event_type, "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
                            "received_at": row.received_at.isoformat(), "verified": row.verified,
                            "processing_status": row.processing_status, "failure_reason": row.failure_reason,
                            "invoice_id": row.invoice_id} for row in recent_webhooks],
    }


@app.get("/paypal/recipients")
def paypal_recipients(request: Request, db: Session = Depends(get_db)) -> dict:
    require_operator(request, db)
    allowed = settings.allowed_recipients
    clients = list(db.scalars(select(Client).where(Client.source == "paypal").order_by(Client.display_name)).all())
    return {"items": [{"id": client.id, "name": client.display_name, "email": client.email}
                      for client in clients if client.email and client.email.lower() in allowed],
            "currency": settings.paypal_sandbox_currency.upper()}


@app.post("/paypal/invoices")
def create_paypal_invoice(
    payload: CreateInvoiceInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", min_length=32, max_length=64),
    db: Session = Depends(get_db),
) -> dict:
    require_operator(request, db)
    if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", idempotency_key):
        raise HTTPException(status_code=422, detail="Idempotency-Key must be a random URL-safe identifier.")
    if settings.paypal_mode.lower() != "sandbox":
        raise HTTPException(status_code=403, detail="Invoice creation is restricted to PayPal Sandbox.")
    client = db.get(Client, payload.client_id)
    if not client or not client.email or client.email.lower() not in settings.allowed_recipients:
        raise HTTPException(status_code=403, detail="Choose a recipient in PAYPAL_ALLOWED_RECIPIENT_EMAILS.")
    currency = settings.paypal_sandbox_currency.upper()
    if len(currency) != 3 or not currency.isalpha():
        raise HTTPException(status_code=503, detail="PAYPAL_SANDBOX_CURRENCY must be a three-letter currency code.")
    issue_date = _today(db, "paypal")
    term_days = {"DUE_ON_RECEIPT": 0, "NET_15": 15, "NET_30": 30}[payload.payment_term_type]
    due_date = issue_date + timedelta(days=term_days)
    request_facts = {
        "client_id": payload.client_id, "amount": format(payload.amount, "f"),
        "payment_term_type": payload.payment_term_type,
        "partial_payment_allowed": payload.partial_payment_allowed,
    }
    request_hash = hashlib.sha256(json.dumps(request_facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    operation = db.get(SandboxInvoiceOperation, idempotency_key)
    now = datetime.now(timezone.utc)
    if operation and operation.request_hash != request_hash:
        raise HTTPException(status_code=409, detail="This Idempotency-Key was already used with different invoice details.")
    if operation and operation.state == "completed":
        return operation.response or {}
    inserted = operation is None
    if inserted:
        invoice_number = f"LP-{issue_date:%Y%m%d}-{uuid4().hex[:8].upper()}"
        operation = SandboxInvoiceOperation(
            idempotency_key=idempotency_key, request_hash=request_hash,
            invoice_number=invoice_number, state="processing", created_at=now, updated_at=now,
        )
        db.add(operation)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            operation = db.get(SandboxInvoiceOperation, idempotency_key)
            if operation is None:
                raise
            inserted = False
    if not inserted:
        assert operation is not None
        if operation.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="This Idempotency-Key was already used with different invoice details.")
        if operation.state == "completed":
            return operation.response or {}
        if operation.state in {"processing", "sending"} and not _sandbox_operation_stale(operation.updated_at, now):
            raise HTTPException(status_code=409, detail="This Sandbox invoice request is already being processed; retry with the same key shortly.")
        create_phase = operation.paypal_invoice_id is None
        next_state = "processing" if create_phase else "sending"
        claimed = db.execute(update(SandboxInvoiceOperation).where(
            SandboxInvoiceOperation.idempotency_key == idempotency_key,
            SandboxInvoiceOperation.state == operation.state,
            SandboxInvoiceOperation.updated_at == operation.updated_at,
        ).values(state=next_state, updated_at=now))
        if claimed.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail="This Sandbox invoice request is already being processed; retry with the same key shortly.")
        db.commit()
        operation = db.get(SandboxInvoiceOperation, idempotency_key)
    assert operation is not None
    adapter = PayPalAdapter()
    try:
        if not operation.paypal_invoice_id:
            draft = adapter.create_invoice(
                client.email, payload.amount, currency, operation.invoice_number, due_date,
                "Thank you for your work with us.", payment_term_type=payload.payment_term_type,
                partial_payment_allowed=payload.partial_payment_allowed, issue_date=issue_date,
                request_id=idempotency_key,
            )
            paypal_id = str(draft.get("id") or "")
            if not paypal_id:
                raise PayPalError("PayPal created a draft without returning its invoice ID.", ambiguous=True)
            upsert_paypal_invoice(db, draft)
            operation.paypal_invoice_id = paypal_id
            operation.state = "created"
            operation.updated_at = datetime.now(timezone.utc)
            db.commit()
            # Claim the send stage with a compare-and-set so duplicate requests
            # cannot deliver the same invoice at the same time.
            claimed = db.execute(update(SandboxInvoiceOperation).where(
                SandboxInvoiceOperation.idempotency_key == idempotency_key,
                SandboxInvoiceOperation.state == "created",
                SandboxInvoiceOperation.paypal_invoice_id == paypal_id,
            ).values(state="sending", updated_at=datetime.now(timezone.utc)))
            if claimed.rowcount != 1:
                db.rollback()
                raise HTTPException(status_code=409, detail="The Sandbox invoice was created and is waiting for its send step; retry with the same key.")
            db.commit()
            operation = db.get(SandboxInvoiceOperation, idempotency_key)
        paypal_id = operation.paypal_invoice_id
        if not paypal_id:
            raise PayPalError("Sandbox invoice creation is still unresolved; retry with the same Idempotency-Key.", ambiguous=True)
        adapter.send_invoice(paypal_id, request_id=f"{idempotency_key}-send")
        invoice = refresh_paypal_invoice(db, adapter, paypal_id)
        response = {"invoice_id": invoice.id, "invoice_number": invoice.invoice_number, "status": invoice.status}
        operation.state = "completed"
        operation.response = response
        operation.updated_at = datetime.now(timezone.utc)
        db.commit()
    except PayPalError as exc:
        db.rollback()
        operation = db.get(SandboxInvoiceOperation, idempotency_key)
        if operation:
            operation.state = "created" if operation.paypal_invoice_id else "reserved"
            operation.updated_at = datetime.now(timezone.utc)
            db.commit()
        raise HTTPException(status_code=502, detail=f"Sandbox invoice request could not be completed. Retry safely with the same Idempotency-Key. {exc}") from exc
    return response


@app.post("/webhooks/paypal")
async def paypal_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Webhook body must be JSON.") from exc
    event_id = str(payload.get("id") or "")
    if not event_id:
        raise HTTPException(status_code=400, detail="PayPal webhook event ID is required.")
    occurred = None
    try:
        if payload.get("create_time"):
            occurred = datetime.fromisoformat(payload["create_time"].replace("Z", "+00:00"))
    except (ValueError, TypeError):
        occurred = None
    event = db.scalar(select(InvoiceEvent).where(InvoiceEvent.provider_event_id == event_id))
    if event is None:
        event = InvoiceEvent(provider_event_id=event_id, event_type=str(payload.get("event_type") or "unknown"),
                             occurred_at=occurred, raw_payload=payload, verified=False, processing_status="received")
        db.add(event)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            event = db.scalar(select(InvoiceEvent).where(InvoiceEvent.provider_event_id == event_id))
    already_processed = bool(event and event.verified and event.processing_status == "processed")
    try:
        verified = PayPalAdapter().verify_webhook(dict(request.headers), payload)
    except PayPalError as exc:
        if already_processed:
            raise HTTPException(status_code=503, detail="Webhook verification is temporarily unavailable.") from exc
        event.processing_status = "verification_retry"
        event.failure_reason = str(exc)[:1000]
        db.commit()
        raise HTTPException(status_code=503, detail="Webhook verification is temporarily unavailable.") from exc
    if not verified:
        if already_processed:
            raise HTTPException(status_code=401, detail="PayPal webhook signature is invalid.")
        event.verified = False
        event.processing_status = "rejected"
        event.failure_reason = "PayPal signature verification did not succeed."
        db.commit()
        raise HTTPException(status_code=401, detail="PayPal webhook signature is invalid.")
    if already_processed:
        if event.raw_payload != payload:
            raise HTTPException(status_code=409, detail="PayPal webhook event ID was already used with a different body.")
        return {"received": True, "duplicate": True, "processed": True}
    event.verified = True
    event.raw_payload = payload
    provider_invoice = webhook_invoice_id(payload)
    if provider_invoice:
        event.invoice_id = db.scalar(select(Invoice.id).where(Invoice.paypal_id == provider_invoice))
    event.processing_status = "received"
    event.failure_reason = None
    db.commit()
    try:
        paypal_id = webhook_invoice_id(payload)
        if not paypal_id:
            raise ValueError("Webhook resource did not include an invoice ID.")
        refreshed_invoice = refresh_paypal_invoice(db, PayPalAdapter(), str(paypal_id))
        event.invoice_id = refreshed_invoice.id
        event.processing_status = "processed"
        event.failure_reason = None
        db.commit()
    except Exception as exc:
        db.rollback()
        event = db.scalar(select(InvoiceEvent).where(InvoiceEvent.provider_event_id == event_id))
        event.processing_status = "retry"
        event.failure_reason = str(exc)[:1000]
        db.commit()
        raise HTTPException(status_code=503, detail="Verified webhook was saved but invoice refresh failed; retry or manual sync is required.") from exc
    return {"received": True, "duplicate": False, "processed": True}


@app.post("/recommendations/{recommendation_id}/skip")
def skip_recommendation(recommendation_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    recommendation = db.get(Recommendation, recommendation_id)
    if not recommendation:
        raise HTTPException(status_code=404, detail="Recommendation not found.")
    invoice = _get_invoice_or_404(db, recommendation.invoice_id)
    _authorize_invoice(request, invoice, db)
    if recommendation.state != "proposed":
        raise HTTPException(status_code=409, detail=f"Recommendation is already {recommendation.state}.")
    before_state = recommendation.state
    recommendation.state = "skipped"
    recommendation.policy_date = recommendation.policy_date or _today(db, invoice.source)
    db.add(AIAudit(kind="action_transition", invoice_id=invoice.id,
                   facts={"actor": session_user(request, db=db) or "synthetic demo operator",
                          "channel": "operator web UI", "before_state": before_state},
                   output={"state": "skipped", "action": recommendation.action,
                           "recommendation_id": recommendation.id}))
    _update_policy_outcomes(db, invoice.id, {"status": "skipped", "action": recommendation.action,
                                            "recorded_at": datetime.now(timezone.utc).isoformat()})
    db.commit()
    return {"state": recommendation.state}


def _begin_attempt(db: Session, recommendation: Recommendation, invoice: Invoice, text_value: str) -> ActionAttempt:
    content = text_value.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Reminder text cannot be empty.")
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    attempt_key = uuid4().hex
    result = db.execute(update(Recommendation).where(
        Recommendation.id == recommendation.id,
        Recommendation.state.in_(["proposed", "failed"]),
        Recommendation.invoice_version == invoice.material_version,
    ).values(state="sending", edited_text=content, approved_text=content,
             approved_hash=content_hash, approved_at=datetime.now(timezone.utc)))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Recommendation is already being processed or its invoice changed.")
    attempt = ActionAttempt(recommendation_id=recommendation.id, attempt_key=attempt_key, state="sending")
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def _finish_attempt(db: Session, attempt: ActionAttempt, recommendation: Recommendation, state: str, reference: str | None = None, error: str | None = None) -> dict:
    attempt.state = state
    attempt.provider_reference = reference
    attempt.error = error
    attempt.finished_at = datetime.now(timezone.utc)
    recommendation.state = state
    if state in {"sent", "applied"} and recommendation.policy_step is not None:
        invoice = db.get(Invoice, recommendation.invoice_id)
        if invoice:
            invoice.reminder_step = max(invoice.reminder_step, recommendation.policy_step + 1)
            if state == "sent":
                invoice.reminder_events = [*(invoice.reminder_events or []), {
                    "sent_at": attempt.finished_at.isoformat(), "tone": recommendation.tone,
                }]
                invoice.material_version += 1
    _update_policy_outcomes(db, recommendation.invoice_id, {
        "status": state, "action": recommendation.action, "recommendation_id": recommendation.id,
        "provider_reference": reference, "recorded_at": attempt.finished_at.isoformat(), "error": error,
    })
    db.commit()
    return {"state": state, "provider_reference": reference, "error": error}


@app.post("/recommendations/{recommendation_id}/send")
def send_recommendation(recommendation_id: str, payload: SendInput, request: Request, db: Session = Depends(get_db)) -> dict:
    require_operator(request, db)
    recommendation = db.get(Recommendation, recommendation_id)
    if not recommendation:
        raise HTTPException(status_code=404, detail="Recommendation not found.")
    invoice = _get_invoice_or_404(db, recommendation.invoice_id)
    if invoice.source != "paypal":
        raise HTTPException(status_code=409, detail="Synthetic records cannot send through PayPal; use the labelled demo flow.")
    if settings.paypal_mode.lower() != "sandbox":
        raise HTTPException(status_code=403, detail="V1 reminder dispatch is restricted to PayPal Sandbox.")
    if (invoice.client.email or "").lower() not in settings.allowed_recipients:
        raise HTTPException(status_code=403, detail="Recipient is not in PAYPAL_ALLOWED_RECIPIENT_EMAILS.")
    try:
        refreshed = refresh_paypal_invoice(db, PayPalAdapter(), invoice.paypal_id or "")
        db.commit()
    except PayPalError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Could not re-check PayPal invoice before approval: {exc}") from exc
    if refreshed.material_version != recommendation.invoice_version:
        raise HTTPException(status_code=409, detail="Invoice changed at PayPal; review the refreshed recommendation before sending.")
    if not _payable(refreshed):
        raise HTTPException(status_code=409, detail="Invoice is paid, closed, disputed, or has no remaining balance.")
    forecast = forecast_invoice(db, refreshed, today=_today(db, refreshed.source), persist=False)
    _check_policy(db, refreshed, forecast)
    if recommendation.invoice_version != refreshed.material_version:
        raise HTTPException(status_code=409, detail="Recommendation is stale; review it again before sending.")
    attempt = _begin_attempt(db, recommendation, refreshed, payload.text)
    try:
        reference = PayPalAdapter().remind_invoice(refreshed.paypal_id or "")
        return _finish_attempt(db, attempt, recommendation, "sent", reference=reference)
    except PayPalError as exc:
        state = "unknown" if exc.ambiguous else "failed"
        return _finish_attempt(db, attempt, recommendation, state, error=str(exc))


@app.post("/recommendations/{recommendation_id}/approve-partial-payment")
def approve_partial_payment(recommendation_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    require_operator(request, db)
    recommendation = db.get(Recommendation, recommendation_id)
    if not recommendation:
        raise HTTPException(status_code=404, detail="Recommendation not found.")
    invoice = _get_invoice_or_404(db, recommendation.invoice_id)
    if invoice.source != "paypal":
        raise HTTPException(status_code=409, detail="Only PayPal Sandbox invoices can update provider payment settings.")
    if recommendation.action != "offer_partial_payment":
        raise HTTPException(status_code=409, detail="This recommendation is not a partial-payment action.")
    if settings.paypal_mode.lower() != "sandbox":
        raise HTTPException(status_code=403, detail="Partial-payment updates are restricted to PayPal Sandbox.")
    if (invoice.client.email or "").lower() not in settings.allowed_recipients:
        raise HTTPException(status_code=403, detail="Recipient is not in PAYPAL_ALLOWED_RECIPIENT_EMAILS.")
    if recommendation.state not in {"proposed", "failed"}:
        raise HTTPException(status_code=409, detail=f"Recommendation is already {recommendation.state}.")
    adapter = PayPalAdapter()
    try:
        refreshed = refresh_paypal_invoice(db, adapter, invoice.paypal_id or "")
        db.commit()
    except PayPalError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Could not re-check PayPal invoice before approval: {exc}") from exc
    if refreshed.material_version != recommendation.invoice_version:
        raise HTTPException(status_code=409, detail="Invoice changed at PayPal; review the refreshed recommendation before approving.")
    if not _payable(refreshed) or refreshed.partial_payment_allowed:
        raise HTTPException(status_code=409, detail="Invoice is closed, already allows partial payment, or has no remaining balance.")
    forecast = forecast_invoice(db, refreshed, today=_today(db, refreshed.source), persist=False)
    last_attempt, unresolved, last_tone, last_payment = _last_attempt(db, refreshed.id)
    current_action = decide(
        refreshed, forecast.get("probability_by_due"), last_attempt, unresolved,
        _today(db, refreshed.source), forecast.get("probabilities", {}).get("14d"),
        last_payment, last_tone, _client_has_partial_history(db, refreshed.client_id),
    )
    if current_action["action"] != "offer_partial_payment" or holdback_assignment(refreshed.id, _today(db, refreshed.source)):
        raise HTTPException(status_code=409, detail="The partial-payment action is no longer recommended by today's policy.")
    if refreshed.id not in _top_policy_candidates(db, refreshed.source):
        raise HTTPException(status_code=409, detail="This invoice is outside today's top five suggestions ranked by assumed 14-day cash uplift (10% scenario, not measured incremental cash).")
    action_text = "Approved: enable PayPal partial payments; no reminder message is sent."
    attempt = _begin_attempt(db, recommendation, refreshed, action_text)
    try:
        adapter.enable_partial_payment(refreshed.paypal_id or "")
    except PayPalError as exc:
        return _finish_attempt(db, attempt, recommendation, "unknown" if exc.ambiguous else "failed", error=str(exc))
    try:
        refreshed = refresh_paypal_invoice(db, adapter, refreshed.paypal_id or "")
    except PayPalError as exc:
        return _finish_attempt(db, attempt, recommendation, "unknown", error=f"PayPal update was accepted but re-sync failed: {exc}")
    if not refreshed.partial_payment_allowed:
        return _finish_attempt(db, attempt, recommendation, "unknown", error="PayPal accepted the update request, but the re-synced invoice does not yet show partial payments enabled.")
    result = _finish_attempt(db, attempt, recommendation, "applied", reference=refreshed.paypal_id)
    return {**result, "note": "Partial payments were enabled in PayPal Sandbox and the invoice was re-synced."}


@app.post("/demo/recommendations/{recommendation_id}/send")
def demo_send(recommendation_id: str, payload: SendInput, db: Session = Depends(get_db)) -> dict:
    recommendation = db.get(Recommendation, recommendation_id)
    if not recommendation:
        raise HTTPException(status_code=404, detail="Recommendation not found.")
    invoice = _get_invoice_or_404(db, recommendation.invoice_id)
    if not _is_demo_source(invoice.source) or invoice.source != _public_demo_source():
        raise HTTPException(status_code=403, detail="Demo simulation is restricted to synthetic invoices.")
    if _historical_demo(db, invoice.source):
        raise HTTPException(409, "Return the demo clock to today before simulating actions.")
    if recommendation.invoice_version != invoice.material_version or not _payable(invoice):
        raise HTTPException(status_code=409, detail="Invoice changed or is no longer payable.")
    if recommendation.action == "offer_partial_payment":
        forecast = forecast_invoice(db, invoice, today=_today(db, invoice.source), persist=False)
        last_attempt, unresolved, last_tone, last_payment = _last_attempt(db, invoice.id)
        decision = decide(
            invoice, forecast.get("probability_by_due"), last_attempt, unresolved,
            _today(db, invoice.source), forecast.get("probabilities", {}).get("14d"),
            last_payment, last_tone, _client_has_partial_history(db, invoice.client_id),
        )
        if decision["action"] != "offer_partial_payment" or invoice.id not in _top_policy_candidates(db, invoice.source):
            raise HTTPException(status_code=409, detail="The partial-payment action is no longer recommended.")
        if holdback_assignment(invoice.id, _today(db, invoice.source)):
            raise HTTPException(status_code=409, detail="This invoice is assigned to today's policy holdback group.")
        text_value = payload.text.strip()
        content_hash = hashlib.sha256(text_value.encode()).hexdigest()
        result = db.execute(update(Recommendation).where(
            Recommendation.id == recommendation.id, Recommendation.state == "proposed",
            Recommendation.invoice_version == invoice.material_version,
        ).values(state="applied", edited_text=text_value, approved_text=text_value,
                 approved_hash=content_hash, approved_at=datetime.now(timezone.utc)))
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail="Recommendation is already processed.")
        invoice.partial_payment_allowed = True
        invoice.material_version += 1
        if recommendation.policy_step is not None:
            invoice.reminder_step = max(invoice.reminder_step, recommendation.policy_step + 1)
        attempt = ActionAttempt(recommendation_id=recommendation.id, attempt_key=uuid4().hex,
                                state="applied", provider_reference=f"synthetic-demo-{uuid4().hex[:12]}",
                                finished_at=datetime.now(timezone.utc))
        db.add(attempt)
        _update_policy_outcomes(db, invoice.id, {
            "status": "applied", "action": recommendation.action, "recommendation_id": recommendation.id,
            "simulated": True, "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        db.commit()
        return {"state": "applied", "simulated": True, "note": "Partial-payment settings were simulated; no PayPal update or email was sent."}
    _check_policy(db, invoice, forecast_invoice(db, invoice, today=_today(db, invoice.source), persist=False), source_scope=invoice.source)
    text_value = payload.text.strip()
    content_hash = hashlib.sha256(text_value.encode()).hexdigest()
    result = db.execute(update(Recommendation).where(
        Recommendation.id == recommendation.id, Recommendation.state == "proposed",
        Recommendation.invoice_version == invoice.material_version,
    ).values(state="sending", edited_text=text_value, approved_text=text_value,
             approved_hash=content_hash, approved_at=datetime.now(timezone.utc)))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Recommendation is already processed.")
    attempt = ActionAttempt(recommendation_id=recommendation.id, attempt_key=uuid4().hex, state="sent",
                            provider_reference=f"synthetic-demo-{uuid4().hex[:12]}", finished_at=datetime.now(timezone.utc))
    db.add(attempt)
    recommendation.state = "sent"
    if recommendation.policy_step is not None:
        invoice.reminder_step = max(invoice.reminder_step, recommendation.policy_step + 1)
    sent_at = datetime.now(timezone.utc)
    invoice.reminder_events = [*(invoice.reminder_events or []), {
        "sent_at": sent_at.isoformat(), "tone": recommendation.tone, "simulated": True,
    }]
    invoice.material_version += 1
    _update_policy_outcomes(db, invoice.id, {
        "status": "sent", "action": recommendation.action, "recommendation_id": recommendation.id,
        "simulated": True, "recorded_at": datetime.now(timezone.utc).isoformat(),
    })
    db.commit()
    return {"state": "sent", "simulated": True, "note": "Synthetic demo only; no PayPal email was sent."}


@app.post("/demo/invoices/{invoice_id}/payment")
def demo_payment(invoice_id: str, payload: PaymentInput, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), db: Session = Depends(get_db)) -> dict:
    invoice = db.scalar(select(Invoice).options(joinedload(Invoice.client)).where(
        Invoice.id == invoice_id,
    ).with_for_update())
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if not _is_demo_source(invoice.source) or invoice.source != _public_demo_source():
        raise HTTPException(status_code=403, detail="Payment simulation is restricted to synthetic invoices.")
    amount = payload.amount if payload.amount is not None else invoice.outstanding
    try:
        amount = Decimal(amount).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        raise HTTPException(status_code=422, detail="Payment amount must be a valid decimal.")
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 128:
            raise HTTPException(status_code=422, detail="Idempotency-Key must contain 1–128 characters.")
        payment_key = f"demo-payment-{idempotency_key}"
        prior = db.scalar(select(Payment).where(Payment.provider_payment_id == payment_key))
        if prior:
            if prior.invoice_id != invoice.id or prior.amount != amount or prior.currency != invoice.currency:
                raise HTTPException(status_code=409, detail="This payment request key was already used for a different payment.")
            return {"invoice_id": invoice.id, "status": invoice.status, "paid": str(invoice.paid),
                    "outstanding": str(invoice.outstanding), "currency": invoice.currency,
                    "simulated": True, "replayed": True,
                    "note": "This payment request was already recorded; the existing result is shown."}
    else:
        payment_key = f"demo-payment-{invoice.id}-{uuid4().hex}"
    if not _payable(invoice):
        raise HTTPException(status_code=409, detail="Invoice cannot accept another payment.")
    if _historical_demo(db, invoice.source):
        raise HTTPException(409, "Return the demo clock to today before simulating payment.")
    if amount <= 0 or amount > invoice.outstanding:
        raise HTTPException(status_code=422, detail="Payment amount must be greater than zero and no more than the balance.")
    invoice.paid += amount
    invoice.outstanding -= amount
    invoice.status = "PAID" if invoice.outstanding == 0 else "PARTIALLY_PAID"
    invoice.material_version += 1
    invoice.synced_at = datetime.now(timezone.utc)
    db.add(Payment(provider_payment_id=payment_key, invoice_id=invoice.id, amount=amount,
                   currency=invoice.currency, occurred_at=datetime.now(timezone.utc), source=invoice.source))
    db.flush()
    _update_policy_outcomes(db, invoice.id, {
        "status": "payment_observed", "payment_amount": str(amount),
        "outstanding": str(invoice.outstanding), "recorded_at": datetime.now(timezone.utc).isoformat(),
    })
    client = db.get(Client, invoice.client_id)
    _drift_for(db, client)
    db.commit()
    return {"invoice_id": invoice.id, "status": invoice.status, "paid": str(invoice.paid),
            "outstanding": str(invoice.outstanding), "currency": invoice.currency,
            "simulated": True, "note": "Synthetic demo only; no PayPal payment was made."}


@app.get("/exports/invoices.csv")
def export_invoices(request: Request, q: str | None = None, view: str = "open", client_id: str | None = None,
                    statuses: str | None = None, due_from: date | None = None, due_to: date | None = None,
                    predicted_week: date | None = None, age_bucket: str | None = None, sort: str = "due",
                    direction: str = "asc", action_only: bool = False, drifting_only: bool = False,
                    as_of: date | None = None, db: Session = Depends(get_db)):
    db.info["export_all"] = True
    result = invoices(request, q=q, page=1, page_size=100, view=view, client_id=client_id,
                      statuses=statuses, due_from=due_from, due_to=due_to, predicted_week=predicted_week,
                      age_bucket=age_bucket, sort=sort, direction=direction, action_only=action_only,
                      drifting_only=drifting_only, as_of=as_of, db=db)
    def rows():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        filters = {
            "search": q or "all", "view": view, "client_id": client_id or "all",
            "statuses": statuses or "all", "due_from": due_from.isoformat() if due_from else "all",
            "due_to": due_to.isoformat() if due_to else "all", "age_bucket": age_bucket or "all",
            "settlement_week": predicted_week.isoformat() if predicted_week else "all",
            "action_only": action_only, "drifting_only": drifting_only,
        }
        writer.writerow(["Snapshot as of", result["as_of"]])
        writer.writerow(["Currency scope", "All source currencies; values are not converted"])
        filters["sort"] = sort
        filters["direction"] = direction
        writer.writerow(["Filters", json.dumps(filters, sort_keys=True, ensure_ascii=False)])
        writer.writerow([])
        writer.writerow(["Invoice", "Client", "Status", "Due", "Currency", "Outstanding", "p10", "p50", "p90"])
        yield buffer.getvalue()
        for item in result["items"]:
            buffer.seek(0)
            buffer.truncate(0)
            quantiles = (item.get("forecast") or {}).get("quantiles", {})
            values = [item["invoice_number"], item["client_name"], item["status"], item["due_date"], item["currency"], item["outstanding"],
                      *[(quantiles.get(key) or {}).get("date", "After 180 days") if item.get("forecast") else "" for key in ("p10", "p50", "p90")]]
            # Spreadsheet formula injection is possible even in quoted cells.
            writer.writerow(["'" + str(value) if str(value).startswith(("=", "+", "-", "@", "\t", "\r")) else value for value in values])
            yield buffer.getvalue()
    return StreamingResponse(rows(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="late-payer-all-filtered.csv"', "Cache-Control":"private, no-store"})


@app.post("/recommendations/{recommendation_id}/draft")
def save_draft(recommendation_id: str, payload: SendInput, request: Request, db: Session = Depends(get_db)):
    row = db.get(Recommendation, recommendation_id)
    if not row:
        raise HTTPException(404, "Recommendation not found.")
    invoice = _get_invoice_or_404(db, row.invoice_id)
    _authorize_invoice(request, invoice, db)
    if row.state not in {"proposed", "failed"} or row.invoice_version != invoice.material_version:
        raise HTTPException(409, "Recommendation is no longer editable.")
    before_text = row.edited_text
    row.edited_text = payload.text
    db.add(AIAudit(kind="draft_edit", invoice_id=invoice.id,
                   facts={"actor": session_user(request, db=db) or "synthetic demo operator", "channel": "operator web UI", "before_text": before_text},
                   output={"text": payload.text, "recommendation_id": row.id}))
    db.commit()
    return {"saved": True}


@app.post("/recommendations/{recommendation_id}/undo-skip")
def undo_skip(recommendation_id: str, request: Request, db: Session = Depends(get_db)):
    row = db.get(Recommendation, recommendation_id)
    if not row:
        raise HTTPException(404, "Recommendation not found.")
    invoice = _get_invoice_or_404(db, row.invoice_id)
    _authorize_invoice(request, invoice, db)
    if row.state != "skipped" or row.invoice_version != invoice.material_version:
        raise HTTPException(409, "Only an unchanged skipped recommendation can be restored.")
    replacement = db.scalar(select(Recommendation.id).where(Recommendation.invoice_id == row.invoice_id,
        Recommendation.id != row.id, Recommendation.state.in_(["proposed", "sending", "sent", "applied"])))
    if replacement:
        raise HTTPException(409, "A replacement action already exists; review the invoice.")
    row.state = "proposed"
    row.policy_date = _today(db, invoice.source)
    db.add(AIAudit(kind="action_transition", invoice_id=invoice.id,
                   facts={"actor": session_user(request, db=db) or "synthetic demo operator",
                          "channel": "operator web UI", "before_state": "skipped"},
                   output={"state": "proposed", "action": row.action,
                           "recommendation_id": row.id}))
    skipped_logs = db.scalars(select(PolicyDecisionLog).where(PolicyDecisionLog.invoice_id == invoice.id,
        PolicyDecisionLog.decision_date == row.policy_date, PolicyDecisionLog.outcome["status"].as_string() == "skipped")).all()
    for entry in skipped_logs:
        previous = entry.outcome or {}
        entry.outcome = {**previous, "status": "pending", "undo_skip_at": datetime.now(timezone.utc).isoformat(),
                         "history": [*previous.get("history", []), {key: value for key, value in previous.items() if key != "history"}]}
    db.commit()
    return {"state": "proposed"}


@app.post("/demo/clock")
def prepare_demo_clock(as_of: date, db: Session = Depends(get_db)):
    if _public_demo_source() != "demo":
        raise HTTPException(410, "Historical dates are loaded as isolated portfolio snapshots for this browser session.")
    state = db.get(DemoState, "demo")
    if not state or not state.as_of_date - timedelta(days=60) <= as_of <= state.as_of_date:
        raise HTTPException(422, "Demo time travel is limited to the latest 60 days.")
    from app.services.projections import refresh
    refresh(db, as_of=as_of)
    db.commit()
    return {"as_of": as_of.isoformat(), "simulation_disabled": as_of != state.as_of_date}
