from __future__ import annotations

import csv
import io
import hashlib
import hmac
import json
import re
import statistics
import logging
from time import perf_counter
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.http_cache import PrivateReadCache, cached_aggregate
from app.telemetry import metrics, timed
from app.config import get_settings
from app.db import get_db
from app.models import ActionAttempt, Client, DemoState, Invoice, InvoiceEvent, Payment, PolicyDecisionLog, Prediction, Recommendation, ReplyTriage, SyncRun, ReadModel, AIAudit
from app.services.ai import classify_reply, extract_promised_date, generate_reminder_draft
from app.security import COOKIE_NAME, create_session, require_operator, session_user
from app.services.dates import business_date
from app.services.drift import client_drift
from app.services.forecast import _as_record, forecast_invoice, warm_model
from app.services.paypal import PayPalAdapter, PayPalError, webhook_invoice_id
from app.services.policy import DAILY_SUGGESTION_CAP, CLOSED_STATUSES, decide, holdback_assignment, render_message
from app.services.sync import refresh_paypal_invoice, sync_paypal, upsert_paypal_invoice
from ml.activity import fit_bgnbd

settings = get_settings()
_REQUEST_DEMO_AS_OF: ContextVar[date | None] = ContextVar("request_demo_as_of", default=None)
ACTIVITY_EXCLUDED_STATUSES = {"CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"}
PORTFOLIO_CONTEXT_KEY = "latepayer_portfolio_context"
_ACTIVITY_CACHE: dict[tuple, dict] = {}
_ACTIVITY_CACHE_LOCK = Lock()
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


@app.middleware("http")
async def demo_time_travel_context(request: Request, call_next):
    override = None
    if request.query_params.get("as_of"):
        try:
            override = date.fromisoformat(request.query_params["as_of"])
        except ValueError:
            pass
    token = _REQUEST_DEMO_AS_OF.set(override)
    timings = {"db": 0.0, "model_load": 0.0, "scoring": 0.0, "drift": 0.0}
    timing_token = metrics.set(timings)
    started = perf_counter()
    try:
        response = await call_next(request)
        timings["total"] = (perf_counter() - started) * 1000
        response.headers["Server-Timing"] = ", ".join(f"{key};dur={value:.2f}" for key, value in timings.items())
        logging.getLogger("uvicorn.error").info("%s %s %s", request.method, request.url.path, timings)
        return response
    finally:
        metrics.reset(timing_token)
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


class AskInput(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class CreateInvoiceInput(BaseModel):
    client_id: str = Field(min_length=1, max_length=36)
    amount: Decimal = Field(gt=Decimal("0"), le=Decimal("1000000"))
    payment_term_type: Literal["DUE_ON_RECEIPT", "NET_15", "NET_30"]
    partial_payment_allowed: bool = False


class WhatIfInput(BaseModel):
    payment_term_type: Literal["NET_15", "NET_30"] = "NET_15"


def _today(db: Session | None = None, source: str | None = None) -> date:
    if source == "demo" and _REQUEST_DEMO_AS_OF.get() is not None:
        return _REQUEST_DEMO_AS_OF.get()
    if db is not None and source == "demo":
        if "demo_clock" in db.info:
            return db.info["demo_clock"].as_of_date
        demo_state = db.get(DemoState, "demo")
        if demo_state:
            # Keep a strong request-local reference: SQLAlchemy's weak identity
            # map otherwise re-queries this row for every invoice/client.
            db.info["demo_clock"] = demo_state
            return demo_state.as_of_date
    try:
        return datetime.now(ZoneInfo(settings.merchant_timezone)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(timezone.utc).date()


def _historical_demo(db: Session, source: str) -> bool:
    override = _REQUEST_DEMO_AS_OF.get()
    if source != "demo" or override is None:
        return False
    state = db.info.get("demo_clock") or db.get(DemoState, "demo")
    if state:
        db.info["demo_clock"] = state
    return bool(state and override != state.as_of_date)


def _verify_login(username: str, password: str) -> bool:
    if not settings.production_auth_configured:
        return False
    expected_user = hmac.compare_digest(username.encode(), settings.operator_username.encode())
    expected_password = hmac.compare_digest(password.encode(), settings.operator_password.encode())
    return expected_user and expected_password


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
    if history_complete and include_predictions and invoice_ids and scoring_dates:
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


def _why_line(forecast: dict | None, drift: dict) -> str:
    if drift.get("available") and drift.get("signal_type") in {"paying_later", "later_than_usual", "gone_quiet"}:
        message = str(drift.get("message") or "").strip()
        if message:
            return message if message.endswith((".", "!", "?")) else f"{message}."
    reasons = (forecast or {}).get("reasons") or []
    if reasons:
        sentence = str(reasons[0]).strip()
        return sentence if sentence.endswith((".", "!", "?")) else f"{sentence}."
    return "This forecast uses the portfolio baseline because no invoice-specific driver is available."


@lru_cache(maxsize=4)
def _backtest_issue_predictions(path: str) -> dict[str, dict]:
    try:
        value = json.loads(Path(path).read_text())
        rows = value.get("backtest", {}).get("invoices", [])
        return {str(row["invoice_id"]): row for row in rows if row.get("invoice_id")}
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def _issue_prediction(db: Session, invoice: Invoice) -> dict | None:
    saved = db.scalar(select(Prediction).where(
        Prediction.invoice_id == invoice.id, Prediction.scoring_date == invoice.issue_date,
    ).limit(1))
    if saved:
        return {
            "source": "stored_issue_prediction", "model_version": saved.model_version,
            "p10_date": (saved.quantiles.get("p10") or {}).get("date"),
            "p50_date": (saved.quantiles.get("p50") or {}).get("date"),
            "p90_date": (saved.quantiles.get("p90") or {}).get("date"),
            "probabilities": saved.probabilities,
        }
    if invoice.source != "demo":
        return None
    artifact = Path(settings.model_path).with_name("eval.json")
    row = _backtest_issue_predictions(str(artifact)).get(invoice.id)
    if not row:
        return None
    return {
        "source": "out_of_sample_issue_backtest", "model_version": None,
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
    if invoice.source == "demo":
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
        action["reason"] = "The daily review capacity is full; this invoice is outside today's top five by expected cash gain."
    recommendation = _latest_recommendation(db, invoice.id)
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
    if include_detail and create_recommendation and (not recommendation or recommendation.invoice_version != invoice.material_version or recommendation.state in {"skipped", "sent", "failed", "applied", "superseded"}) and action["action"] in {"friendly", "firm", "offer_partial_payment"}:
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
        "why": _why_line(forecast, drift),
        "recommendation": _recommendation_dict(recommendation),
        "days_until_due_nonnegative": currency_due,
    }
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
            if invoice.source == "demo"
            else "PayPal sends its built-in invoice reminder; the approved editable copy is retained for review/audit and is not transmitted as the reminder body."
        )
    if not include_detail and summary["forecast"]:
        summary["forecast"] = {key: value for key, value in summary["forecast"].items()
                               if key in {"quantiles", "probabilities", "model", "model_version", "limited_history", "display_label", "collection_probability"}}
    return summary


def _recommendation_dict(recommendation: Recommendation | None) -> dict | None:
    if not recommendation:
        return None
    return {
        "id": recommendation.id, "action": recommendation.action, "tone": recommendation.tone,
        "reason": recommendation.reason, "draft_text": recommendation.draft_text,
        "draft_source": recommendation.draft_source,
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


def _authorize_invoice(request: Request, invoice: Invoice) -> str | None:
    user = session_user(request)
    if invoice.source != "demo" and not user:
        raise HTTPException(status_code=401, detail="Operator login required to view PayPal invoices.")
    return user


def _payable(invoice: Invoice) -> bool:
    return invoice.status not in CLOSED_STATUSES and not invoice.disputed and invoice.outstanding > 0


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
            candidates.append((action["expected_cash_gain"], invoice.due_date, invoice.id))
    candidates.sort(key=lambda row: (-row[0], row[1], row[2]))
    return [invoice_id for _, _, invoice_id in candidates]


def _top_policy_candidates(db: Session, source: str | None = None) -> set[str]:
    return set(_rank_policy_candidates(db, source)[:DAILY_SUGGESTION_CAP])


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
        raise HTTPException(status_code=409, detail="This invoice is outside today's top five suggestions by expected cash gain.")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/demo/reset")
def reset_demo(db: Session = Depends(get_db)) -> dict:
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
    payload["available"] = True
    return payload


@app.get("/ready")
def ready(db: Session = Depends(get_db)) -> dict:
    if not settings.production_auth_configured:
        raise HTTPException(status_code=503, detail="Production operator credentials and session secret must be configured.")
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is not ready.") from exc
    return {
        "status": "ready", "database": "ok", "paypal_mode": settings.paypal_mode,
        "operator_auth_configured": settings.production_auth_configured,
        "paypal_credentials_configured": settings.live_configured,
        "paypal_webhook_configured": bool(settings.paypal_webhook_id),
        "model_artifact_configured": __import__("pathlib").Path(settings.model_path).exists(),
    }


@app.get("/auth/me")
def auth_me(request: Request) -> dict:
    user = session_user(request)
    return {"authenticated": bool(user), "username": user}


@app.post("/auth/login")
def login(payload: LoginInput, response: Response) -> dict:
    if not _verify_login(payload.username, payload.password):
        raise HTTPException(status_code=401, detail="Username or password is incorrect.")
    response.set_cookie(
        COOKIE_NAME, create_session(), max_age=settings.session_max_age_seconds, path="/",
        httponly=True, secure=settings.app_env == "production", samesite="lax",
    )
    return {"authenticated": True, "username": settings.operator_username}


@app.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=settings.app_env == "production", samesite="lax")
    return {"authenticated": False}


def _age_bucket(invoice: Invoice, today: date) -> str | None:
    if not _payable(invoice):
        return None
    days_late = (today - invoice.due_date).days
    if days_late <= 0:
        return "not_due"
    if days_late <= 30:
        return "late_1_30"
    if days_late <= 60:
        return "late_31_60"
    if days_late <= 90:
        return "late_61_90"
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
    user = session_user(request)
    page = max(1, page)
    if limit is not None and page_size == 25:
        page_size = limit
    if not 1 <= page_size <= 100:
        raise HTTPException(status_code=422, detail="page_size must be between 1 and 100.")
    if due_from and due_to and due_from > due_to:
        raise HTTPException(status_code=422, detail="Due date range is reversed.")
    allowed_views = {"open", "due_this_week", "overdue", "paid", "all"}
    view = view if view in allowed_views else "open"
    allowed_buckets = {"not_due", "late_1_30", "late_31_60", "late_61_90", "late_90_plus"}
    age_bucket = age_bucket if age_bucket in allowed_buckets else None
    allowed_sorts = {"action", "expected_cash", "p14", "due", "client", "balance", "invoice", "status", "predicted", "drift"}
    sort = sort if sort in allowed_sorts else "action"
    direction = "desc" if direction.casefold() == "desc" else "asc"
    demo_state = db.get(DemoState, "demo")
    as_of_max = demo_state.as_of_date if demo_state else _today(db, "demo")
    if as_of is not None and (as_of > as_of_max or as_of < as_of_max - timedelta(days=60)):
        raise HTTPException(status_code=422, detail="Demo time travel is limited to the latest 60 days.")

    source_names = ["demo"] if not user else ["demo", "paypal"]
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
    candidate_order = _rank_policy_candidates(db, None if user else "demo", forecast_cache)
    daily_candidate_ids = set(candidate_order[:DAILY_SUGGESTION_CAP])
    candidate_ranks = {invoice_id: index for index, invoice_id in enumerate(candidate_order[:DAILY_SUGGESTION_CAP], start=1)}

    clients_in_scope = list(db.scalars(select(Client).where(Client.source.in_(source_names))).all())
    drift_cache: dict[str, dict] = {}
    for client in clients_in_scope:
        drift_cache[client.id] = _drift_for(db, client, activity_by_source.get(client.source, {}).get(client.id))
    drifting_ids = {client_id for client_id, drift in drift_cache.items()
                    if drift.get("signal_type") in {"paying_later", "later_than_usual", "gone_quiet"}}

    ready_recommendations = set()
    for source in source_names:
        today = _today(db, source)
        ready_recommendations.update(db.scalars(select(Recommendation.invoice_id).join(Invoice).where(
            Invoice.source == source, Recommendation.state == "proposed",
            Recommendation.action.in_(["friendly", "firm", "offer_partial_payment"]),
            Recommendation.policy_date == today,
        )).all())
    open_condition = and_(Invoice.status.not_in(CLOSED_STATUSES), Invoice.outstanding > 0, Invoice.disputed.is_(False))
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
        rows = [row for row in base_rows if _payable(row)]
    elif view == "due_this_week":
        rows = [row for row in base_rows if _payable(row) and 0 <= (row.due_date - _today(db, row.source)).days <= 7]
    elif view == "overdue":
        rows = [row for row in base_rows if _payable(row) and row.due_date < _today(db, row.source)]
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
        week_end = predicted_week + timedelta(days=7)
        items = [item for item in items if predicted_week.isoformat() <= ((item.get("forecast") or {}).get("quantiles", {}).get("p50") or {}).get("date", "") < week_end.isoformat()]
    if action_only:
        items = [item for item in items if item["id"] in ready_recommendations]
    _sort_invoice_items(items, sort, direction)

    totals: dict[str, Decimal] = {}
    if sql_page and view == "all":
        for row in base_rows:
            if row.status not in CLOSED_STATUSES and row.outstanding > 0:
                totals[row.currency] = totals.get(row.currency, Decimal("0")) + row.outstanding
    for item in [] if sql_page else items:
        if Decimal(item["outstanding"]) > 0 and item["status"] not in CLOSED_STATUSES:
            totals[item["currency"]] = totals.get(item["currency"], Decimal("0")) + Decimal(item["outstanding"])
    def build_aggregates():
        aging = {name: {"count": 0, "amounts": {}} for name in allowed_buckets}
        for row in base_rows:
            bucket = _age_bucket(row, _today(db, row.source))
            if not bucket:
                continue
            aging[bucket]["count"] += 1
            amounts = aging[bucket]["amounts"]
            amounts[row.currency] = str(Decimal(amounts.get(row.currency, "0")) + Decimal(row.outstanding))

        expected_cash: dict[tuple[str, str], dict] = {}
        for row in base_rows:
            if not _payable(row):
                continue
            forecast = forecast_cache.get(row.id) or forecast_invoice(db, row, today=_today(db, row.source), persist=True, detail=False)
            forecast_cache[row.id] = forecast
            p50 = forecast.get("quantiles", {}).get("p50")
            if not p50 or not p50.get("date"):
                continue
            pay_date = date.fromisoformat(p50["date"])
            today = _today(db, row.source)
            if not today <= pay_date <= today + timedelta(days=56):
                continue
            week_start = pay_date - timedelta(days=pay_date.weekday())
            key = (week_start.isoformat(), row.currency)
            entry = expected_cash.setdefault(key, {"week_start": key[0], "currency": row.currency, "amount": Decimal("0"), "invoice_count": 0})
            entry["amount"] += Decimal(row.outstanding) * Decimal(str(forecast.get("collection_probability", 1)))
            entry["invoice_count"] += 1
        return aging, expected_cash

    aggregate_key = (tuple(source_names), tuple((source, _today(db, source)) for source in source_names),
                     view, q, client_id, statuses, due_from, due_to)
    aging, expected_cash = cached_aggregate(aggregate_key, build_aggregates)

    filtered_count = view_counts[view] if sql_page else len(items)
    page_count = max(1, (filtered_count + page_size - 1) // page_size)
    page_items = items if sql_page or db.info.get("export_all") else items[(page - 1) * page_size:page * page_size]
    model_names = sorted({item["forecast"]["model"] for item in page_items if item["forecast"]})
    ready_for_review_count = len(ready_recommendations)
    slim_items = []
    for item in page_items:
        slim = {key: item[key] for key in ("id", "invoice_number", "client_id", "client_name", "due_date", "days_to_due", "outstanding", "currency", "status", "source")}
        slim["drift"] = {"signal": item["drift"].get("signal"), "signal_type":item["drift"].get("signal_type")}
        slim["decision"] = {key: item["decision"].get(key) for key in ("action", "expected_cash_gain", "expected_cash_gain_currency", "daily_rank")}
        forecast = item["forecast"]
        slim["forecast"] = {key: forecast.get(key) for key in ("quantiles", "model", "limited_history", "display_label")} if forecast else None
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
        "models": model_names, "synthetic_demo": not user,
        "ready_for_review_count": ready_for_review_count,
        "drifting_client_count": len(drifting_ids),
        "client_signals": sorted([{"id": client.id, "name":client.display_name, "drift":drift_cache[client.id]}
                                  for client in clients_in_scope if client.id in drifting_ids],
                                 key=lambda row: -row["drift"].get("severity_score", 0))[:5],
        "as_of": _today(db, "demo").isoformat(), "as_of_max": as_of_max.isoformat(),
    }


@app.get("/invoices/{invoice_id}")
def invoice_detail(invoice_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    _authorize_invoice(request, invoice)
    activity_scores = _activity_for_source(db, invoice.source)
    daily_candidate_ids = _top_policy_candidates(db, None if session_user(request) else "demo")
    payload = _invoice_dict(db, invoice, include_detail=True, activity_scores=activity_scores,
                            daily_candidate_ids=daily_candidate_ids)
    _record_policy_decision(db, invoice, payload)
    db.commit()
    return payload


@app.get("/action-queue")
def action_queue(request: Request, db: Session = Depends(get_db)) -> dict:
    scope = None if session_user(request) else "demo"
    query = select(Invoice).options(joinedload(Invoice.client)).where(Invoice.status.not_in(CLOSED_STATUSES), Invoice.outstanding > 0)
    if scope:
        query = query.where(Invoice.source == scope)
    rows = list(db.scalars(query).all())
    _prepare_portfolio_context(db, rows)
    reviewed_today: set[str] = set()
    for source in {row.source for row in rows}:
        today = _today(db, source)
        reviewed_today.update(db.scalars(select(Recommendation.invoice_id).join(Invoice).where(
            Invoice.source == source, Recommendation.state.in_(["skipped", "sent", "applied"]),
            Recommendation.policy_date == today,
        )).all())
    forecast_cache: dict[str, dict] = {}
    ranked = [invoice_id for invoice_id in _rank_policy_candidates(db, scope, forecast_cache) if invoice_id not in reviewed_today]
    ranked = ranked[:DAILY_SUGGESTION_CAP]
    candidate_ids = set(ranked)
    items = []
    for invoice_id in ranked[:DAILY_SUGGESTION_CAP]:
        invoice = next((row for row in rows if row.id == invoice_id), None)
        if invoice is None:
            continue
        item = _invoice_dict(db, invoice, include_detail=True, include_timeline=False, daily_candidate_ids=candidate_ids,
                             forecast_cache=forecast_cache)
        if item.get("recommendation") and item["recommendation"].get("state") == "proposed":
            _record_policy_decision(db, invoice, item)
            items.append({key: item[key] for key in ("id", "invoice_number", "client_name", "client_id", "source", "currency", "outstanding", "due_date", "decision", "recommendation")})
    items.sort(key=lambda row: (-float(row["decision"].get("expected_cash_gain") or 0), row["due_date"], row["invoice_number"]))
    db.commit()
    skipped = list(db.scalars(select(Recommendation).join(Invoice).where(
        Recommendation.state == "skipped", Invoice.source == "demo" if scope else text("1=1"),
        or_(*[and_(Invoice.source == source, Recommendation.policy_date == _today(db, source)) for source in (["demo"] if scope else ["demo", "paypal"])]),
    )).all())
    return {"items": items, "count": len(items), "pending_count": len(items), "skipped_count": len(skipped),
            "skipped": [{"id": row.id, "invoice_id": row.invoice_id} for row in skipped], "daily_cap": DAILY_SUGGESTION_CAP,
            "as_of": _today(db, scope).isoformat() if scope else _today(db).isoformat(),
            "synthetic_demo": scope == "demo"}


@app.post("/invoices/{invoice_id}/draft")
def draft_invoice_reminder(invoice_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    operator = _authorize_invoice(request, invoice)
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
    body, source = generate_reminder_draft(facts, fallback)
    recommendation.draft_text = body
    recommendation.draft_source = source
    recommendation.drafted_at = datetime.now(timezone.utc)
    recommendation.edited_text = body
    db.add(AIAudit(kind="draft", invoice_id=invoice.id, facts=facts, output={"text":body, "source":source}))
    db.commit()
    return {
        "recommendation": _recommendation_dict(recommendation),
        "grounded_in": f"Invoice {invoice.invoice_number}, {invoice.currency} {invoice.outstanding}, due {invoice.due_date.isoformat()}, reminder history and client settlement facts",
        "note": "Gemini draft created. Review and edit it before approval." if source == "gemini" else "Gemini was unavailable or its draft failed validation; the grounded template is shown instead.",
    }


@app.post("/invoices/{invoice_id}/reply")
def triage_invoice_reply(invoice_id: str, payload: ReplyInput, request: Request, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    operator = _authorize_invoice(request, invoice)
    reply = payload.reply.strip()
    if not reply:
        raise HTTPException(status_code=422, detail="Paste the client's reply first.")
    if invoice.source == "paypal" and not operator:
        raise HTTPException(status_code=401, detail="Operator login required to record PayPal client replies.")
    classification, classifier = classify_reply(reply)
    promised_date = extract_promised_date(reply, _today(db, invoice.source)) if classification == "promise_to_pay" else None
    changed = False
    if classification == "dispute" and not invoice.disputed:
        invoice.disputed = True
        invoice.promised_payment_date = None
        changed = True
    elif classification == "promise_to_pay" and promised_date != invoice.promised_payment_date:
        invoice.promised_payment_date = promised_date
        changed = True
    if changed:
        invoice.material_version += 1
    triage = ReplyTriage(
        invoice_id=invoice.id, reply_text=reply, classification=classification,
        promised_payment_date=promised_date, classifier=classifier,
    )
    db.add(triage)
    db.commit()
    db.refresh(triage)
    paused = invoice.disputed or bool(promised_date and _today(db, invoice.source) <= promised_date)
    return {
        "id": triage.id,
        "classification": classification,
        "classifier": classifier,
        "promised_payment_date": promised_date.isoformat() if promised_date else None,
        "reminders_paused": paused,
        "note": "Reminders are paused while this invoice is disputed." if invoice.disputed else (
            f"Reminders are paused until {promised_date.isoformat()}." if paused and promised_date else
            "Reply recorded; no reminder pause is active."
        ),
    }


@app.get("/clients")
def clients(request: Request, q: str | None = None, options_only: bool = False, db: Session = Depends(get_db)) -> dict:
    user = session_user(request)
    query = select(Client).order_by(Client.display_name.asc())
    if not user:
        query = query.where(Client.source == "demo")
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
              for name in ("paying_later", "later_than_usual", "gone_quiet", "insufficient_history")}
    return {"items": output, "signal_counts": counts}


@app.post("/clients/{client_id}/what-if")
def client_terms_what_if(client_id: str, payload: WhatIfInput, request: Request, db: Session = Depends(get_db)) -> dict:
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")
    if client.source != "demo" and not session_user(request):
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
                "p50": forecast["quantiles"]["p50"], "probabilities": forecast["probabilities"],
                "model": forecast["model"], "limited_history": forecast["limited_history"]}

    baseline = score("NET_30", False)
    term_scenario = score(payload.payment_term_type, False)
    partial_scenario = score(payload.payment_term_type, True)
    return {"as_of": as_of.isoformat(), "client_id": client.id, "client_name": client.display_name,
            "currency": currency, "representative_amount": str(Decimal(str(amount)).quantize(Decimal("0.01"))),
            "baseline": baseline, "with_terms": term_scenario, "with_partial_payment": partial_scenario}


def _scoped_invoice_rows(request: Request, db: Session) -> list[Invoice]:
    query = select(Invoice).options(joinedload(Invoice.client)).order_by(Invoice.due_date.asc())
    if not session_user(request):
        query = query.where(Invoice.source == "demo")
    return list(db.scalars(query).all())


def _scoped_clients(request: Request, db: Session) -> list[Client]:
    query = select(Client).order_by(Client.display_name.asc())
    if not session_user(request):
        query = query.where(Client.source == "demo")
    return list(db.scalars(query).all())


@app.get("/facts/invoices/{invoice_id}")
def invoice_facts(invoice_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    _authorize_invoice(request, invoice)
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
    if client.source != "demo" and not session_user(request):
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
        settled_at = history_rows[row.id].get("settled_at")
        values["settled_at"] = settled_at.isoformat() if settled_at else None
        values["days_late"] = (settled_at - row.due_date).days if settled_at else None
        invoice_facts.append(values)
    return {
        "as_of": _today(db, client.source).isoformat(),
        "client": {"id": client.id, "name": client.display_name, "source": client.source},
        "history": history,
        "drift": drift,
        "invoices": invoice_facts,
    }


@app.get("/facts/morning-brief")
def morning_brief(request: Request, db: Session = Depends(get_db)) -> dict:
    rows = _scoped_invoice_rows(request, db)
    open_rows = [row for row in rows if _payable(row)]
    candidate_ids = _top_policy_candidates(db, None if session_user(request) else "demo")
    forecast_cache: dict[str, dict] = {}
    totals: dict[str, Decimal] = {}
    invoice_counts: dict[str, int] = {}
    candidates = []
    today = _today(db, "demo" if not session_user(request) else None)
    week_end = today + timedelta(days=6 - today.weekday())
    for row in open_rows:
        item = _invoice_dict(db, row, daily_candidate_ids=candidate_ids, forecast_cache=forecast_cache,
                             create_recommendation=False)
        p50 = (item.get("forecast") or {}).get("quantiles", {}).get("p50")
        if p50 and p50.get("date"):
            settlement = date.fromisoformat(p50["date"])
            row_today = _today(db, row.source)
            row_week_end = row_today + timedelta(days=6 - row_today.weekday())
            if row_today <= settlement <= row_week_end:
                totals[row.currency] = totals.get(row.currency, Decimal("0")) + Decimal(item["outstanding"])
                invoice_counts[row.currency] = invoice_counts.get(row.currency, 0) + 1
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
            {"currency": currency, "amount": str(amount), "invoice_count": invoice_counts[currency]}
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
    facts = {"as_of": _today(db, "demo" if not session_user(request) else None).isoformat(),
             "invoices": [{"id": row.id, "client_id": row.client_id, "client_name": row.client.display_name,
                            "status": row.status, "currency":row.currency, "outstanding":str(row.outstanding),
                            "due_date":row.due_date.isoformat()} for row in rows]}
    def grounded(value):
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
        for row in rows:
            if row.currency == currency and _payable(row):
                balances[row.client_id] = balances.get(row.client_id, Decimal("0")) + row.outstanding
                counts[row.client_id] = counts.get(row.client_id, 0) + 1
                names[row.client_id] = row.client.display_name
        winner = max(balances, key=balances.get) if balances else None
        data = None if winner is None else {
            "client_id": winner, "client_name": names[winner], "currency": currency,
            "outstanding": str(balances[winner]), "open_invoice_count": counts[winner],
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
                                "severity_score": signal.get("severity_score", 0)})
        signals.sort(key=lambda item: (-float(item["severity_score"]), item["client_name"].casefold()))
        names = ", ".join(item["client_name"] for item in signals[:5])
        answer = f"Clients with timing signals: {names}." if names else "No client has a current delay or cadence signal."
        return grounded({"intent": "clients_slowing_down", "answer": answer, "data": signals[:5]})

    if re.search(r"\b(cash|payments?|settlements?)\b", lowered) and re.search(r"\b(this week|next 7 days)\b", lowered):
        totals: dict[str, Decimal] = {}
        counts: dict[str, int] = {}
        for row in rows:
            if not _payable(row):
                continue
            today = _today(db, row.source)
            forecast = forecast_invoice(db, row, today=today, persist=True)
            p50 = forecast.get("quantiles", {}).get("p50") or {}
            if p50.get("date"):
                expected = date.fromisoformat(p50["date"])
                if today <= expected <= today + timedelta(days=7):
                    totals[row.currency] = totals.get(row.currency, Decimal("0")) + row.outstanding
                    counts[row.currency] = counts.get(row.currency, 0) + 1
        data = [{"currency": key, "amount": str(value), "invoice_count": counts[key]} for key, value in sorted(totals.items())]
        answer = "Expected p50 settlement balances in the next week: " + (", ".join(f"{item['currency']} {Decimal(item['amount']):,.2f}" for item in data) if data else "none.")
        return grounded({"intent": "expected_cash_this_week", "answer": answer, "data": data})

    if "overdue" in lowered:
        overdue = [row for row in rows if _payable(row) and row.due_date < _today(db, row.source) and (not currency or row.currency == currency)]
        overdue.sort(key=lambda row: (-row.outstanding, row.due_date))
        data = [{"invoice_id": row.id, "invoice_number": row.invoice_number, "client_name": row.client.display_name,
                 "currency": row.currency, "outstanding": str(row.outstanding), "due_date": row.due_date.isoformat()}
                for row in overdue[:5]]
        answer = f"There are {len(overdue)} overdue open invoices" + (f" in {currency}" if currency else "") + "."
        return grounded({"intent": "overdue_invoices", "answer": answer, "data": data})

    return grounded({
        "intent": "unsupported", "answer": "I can answer largest open balance by currency, clients with timing signals, expected cash this week, or overdue invoices.",
        "data": None,
    })


@app.post("/sync")
def sync(request: Request, db: Session = Depends(get_db)) -> dict:
    require_operator(request)
    try:
        return sync_paypal(db, PayPalAdapter())
    except PayPalError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/sync/status")
def sync_status(request: Request, db: Session = Depends(get_db)) -> dict:
    require_operator(request)
    latest = db.scalar(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1))
    webhook_failures = db.scalar(select(func.count(InvoiceEvent.id)).where(
        InvoiceEvent.verified.is_(True), InvoiceEvent.processing_status.in_(["retry", "verification_retry"]),
    )) or 0
    return {
        "last_run": ({
            "started_at": latest.started_at.isoformat(), "completed_at": latest.completed_at.isoformat() if latest.completed_at else None,
            "status": latest.status, "created": latest.created_count, "updated": latest.updated_count,
            "event_refreshes": latest.event_refreshes, "retry_failures": latest.retry_failures,
            "errors": (latest.errors or [])[:5],
        } if latest else None),
        "webhook_failures": int(webhook_failures),
    }


@app.get("/paypal/recipients")
def paypal_recipients(request: Request, db: Session = Depends(get_db)) -> dict:
    require_operator(request)
    allowed = settings.allowed_recipients
    clients = list(db.scalars(select(Client).where(Client.source == "paypal").order_by(Client.display_name)).all())
    return {"items": [{"id": client.id, "name": client.display_name, "email": client.email}
                      for client in clients if client.email and client.email.lower() in allowed],
            "currency": settings.paypal_sandbox_currency.upper()}


@app.post("/paypal/invoices")
def create_paypal_invoice(payload: CreateInvoiceInput, request: Request, db: Session = Depends(get_db)) -> dict:
    require_operator(request)
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
    invoice_number = f"LP-{issue_date:%Y%m%d}-{uuid4().hex[:8].upper()}"
    adapter = PayPalAdapter()
    try:
        draft = adapter.create_invoice(
            client.email, payload.amount, currency, invoice_number, due_date,
            "Thank you for your work with us.", payment_term_type=payload.payment_term_type,
            partial_payment_allowed=payload.partial_payment_allowed, issue_date=issue_date,
        )
        paypal_id = str(draft.get("id") or "")
        if not paypal_id:
            raise PayPalError("PayPal created a draft without returning its invoice ID.", ambiguous=True)
        invoice, _ = upsert_paypal_invoice(db, draft)
        db.commit()
        adapter.send_invoice(paypal_id)
        invoice = refresh_paypal_invoice(db, adapter, paypal_id)
        db.commit()
    except PayPalError as exc:
        db.commit()
        detail = str(exc)
        if "invoice_number" in locals():
            detail = f"Sandbox invoice {invoice_number} may need reconciliation before creating another invoice. {detail}"
        raise HTTPException(status_code=502, detail=detail) from exc
    return {"invoice_id": invoice.id, "invoice_number": invoice.invoice_number, "status": invoice.status}


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
    if event and event.verified and event.processing_status == "processed":
        return {"received": True, "duplicate": True, "processed": True}
    if event is None:
        event = InvoiceEvent(provider_event_id=event_id, event_type=str(payload.get("event_type") or "unknown"),
                             occurred_at=occurred, raw_payload=payload, verified=False, processing_status="received")
        db.add(event)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            event = db.scalar(select(InvoiceEvent).where(InvoiceEvent.provider_event_id == event_id))
    try:
        verified = PayPalAdapter().verify_webhook(dict(request.headers), payload)
    except PayPalError as exc:
        event.processing_status = "verification_retry"
        event.failure_reason = str(exc)[:1000]
        db.commit()
        raise HTTPException(status_code=503, detail="Webhook verification is temporarily unavailable.") from exc
    event.verified = verified
    if not verified:
        event.processing_status = "rejected"
        event.failure_reason = "PayPal signature verification did not succeed."
        db.commit()
        raise HTTPException(status_code=401, detail="PayPal webhook signature is invalid.")
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
    _authorize_invoice(request, invoice)
    if recommendation.state != "proposed":
        raise HTTPException(status_code=409, detail=f"Recommendation is already {recommendation.state}.")
    recommendation.state = "skipped"
    recommendation.policy_date = recommendation.policy_date or _today(db, invoice.source)
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
    require_operator(request)
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
    require_operator(request)
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
        raise HTTPException(status_code=409, detail="This invoice is outside today's top five suggestions by expected cash gain.")
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
    if invoice.source != "demo":
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
        if decision["action"] != "offer_partial_payment" or invoice.id not in _top_policy_candidates(db, "demo"):
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
    _check_policy(db, invoice, forecast_invoice(db, invoice, today=_today(db, invoice.source), persist=False), source_scope="demo")
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
    _update_policy_outcomes(db, invoice.id, {
        "status": "sent", "action": recommendation.action, "recommendation_id": recommendation.id,
        "simulated": True, "recorded_at": datetime.now(timezone.utc).isoformat(),
    })
    db.commit()
    return {"state": "sent", "simulated": True, "note": "Synthetic demo only; no PayPal email was sent."}


@app.post("/demo/invoices/{invoice_id}/payment")
def demo_payment(invoice_id: str, payload: PaymentInput, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    if invoice.source != "demo":
        raise HTTPException(status_code=403, detail="Payment simulation is restricted to synthetic invoices.")
    if not _payable(invoice):
        raise HTTPException(status_code=409, detail="Invoice cannot accept another payment.")
    if _historical_demo(db, invoice.source):
        raise HTTPException(409, "Return the demo clock to today before simulating payment.")
    amount = payload.amount if payload.amount is not None else invoice.outstanding
    try:
        amount = Decimal(amount).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        raise HTTPException(status_code=422, detail="Payment amount must be a valid decimal.")
    if amount <= 0 or amount > invoice.outstanding:
        raise HTTPException(status_code=422, detail="Payment amount must be greater than zero and no more than the balance.")
    payment_key = f"demo-payment-{invoice.id}-{uuid4().hex}"
    invoice.paid += amount
    invoice.outstanding -= amount
    invoice.status = "PAID" if invoice.outstanding == 0 else "PARTIALLY_PAID"
    invoice.material_version += 1
    invoice.synced_at = datetime.now(timezone.utc)
    db.add(Payment(provider_payment_id=payment_key, invoice_id=invoice.id, amount=amount,
                   currency=invoice.currency, occurred_at=datetime.now(timezone.utc), source="demo"))
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
    _authorize_invoice(request, invoice)
    if row.state != "proposed" or row.invoice_version != invoice.material_version:
        raise HTTPException(409, "Recommendation is no longer editable.")
    row.edited_text = payload.text
    db.commit()
    return {"saved": True}


@app.post("/recommendations/{recommendation_id}/undo-skip")
def undo_skip(recommendation_id: str, request: Request, db: Session = Depends(get_db)):
    row = db.get(Recommendation, recommendation_id)
    if not row:
        raise HTTPException(404, "Recommendation not found.")
    invoice = _get_invoice_or_404(db, row.invoice_id)
    _authorize_invoice(request, invoice)
    if row.state != "skipped" or row.invoice_version != invoice.material_version:
        raise HTTPException(409, "Only an unchanged skipped recommendation can be restored.")
    replacement = db.scalar(select(Recommendation.id).where(Recommendation.invoice_id == row.invoice_id,
        Recommendation.id != row.id, Recommendation.state.in_(["proposed", "sending", "sent", "applied"])))
    if replacement:
        raise HTTPException(409, "A replacement action already exists; review the invoice.")
    row.state = "proposed"
    row.policy_date = _today(db, invoice.source)
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
    state = db.get(DemoState, "demo")
    if not state or not state.as_of_date - timedelta(days=60) <= as_of <= state.as_of_date:
        raise HTTPException(422, "Demo time travel is limited to the latest 60 days.")
    from app.services.projections import refresh
    refresh(db, as_of=as_of)
    db.commit()
    return {"as_of": as_of.isoformat(), "simulation_disabled": as_of != state.as_of_date}
