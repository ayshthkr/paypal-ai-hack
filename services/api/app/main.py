from __future__ import annotations

import hashlib
import hmac
import json
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import get_db
from app.models import ActionAttempt, Client, DemoState, Invoice, InvoiceEvent, Payment, PolicyDecisionLog, Recommendation, ReplyTriage
from app.services.ai import classify_reply, extract_promised_date, generate_reminder_draft
from app.security import COOKIE_NAME, create_session, require_operator, session_user
from app.services.drift import client_drift
from app.services.forecast import _as_record, forecast_invoice
from app.services.paypal import PayPalAdapter, PayPalError, webhook_invoice_id
from app.services.policy import DAILY_SUGGESTION_CAP, CLOSED_STATUSES, decide, holdback_assignment, render_message
from app.services.sync import refresh_paypal_invoice, sync_paypal
from ml.activity import fit_bgnbd

settings = get_settings()
ACTIVITY_EXCLUDED_STATUSES = {"CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"}
app = FastAPI(title="Late Payer", version="1.0.0", description="Invoice payment forecasts and approved PayPal reminders.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Requested-With"],
)


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


def _today(db: Session | None = None, source: str | None = None) -> date:
    if db is not None and source == "demo":
        demo_state = db.get(DemoState, "demo")
        if demo_state:
            return demo_state.as_of_date
    try:
        return datetime.now(ZoneInfo(settings.merchant_timezone)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(timezone.utc).date()


def _verify_login(username: str, password: str) -> bool:
    if not settings.production_auth_configured:
        return False
    expected_user = hmac.compare_digest(username.encode(), settings.operator_username.encode())
    expected_password = hmac.compare_digest(password.encode(), settings.operator_password.encode())
    return expected_user and expected_password


def _client_records(db: Session, client_id: str) -> list[dict]:
    rows = list(db.scalars(select(Invoice).where(Invoice.client_id == client_id)).all())
    ids = [row.id for row in rows]
    payments = list(db.scalars(select(Payment).where(Payment.invoice_id.in_(ids))).all()) if ids else []
    grouped: dict[str, list[Payment]] = {}
    for payment in payments:
        grouped.setdefault(payment.invoice_id, []).append(payment)
    return [_as_record(row, grouped.get(row.id, [])) for row in rows]


def _activity_for_source(db: Session, source: str) -> dict[str, float]:
    rows = list(db.execute(select(Invoice.client_id, Invoice.issue_date).where(
        Invoice.source == source, Invoice.status.not_in(ACTIVITY_EXCLUDED_STATUSES),
    )).all())
    issue_dates: dict[str, list[date]] = {}
    for client_id, issue_date in rows:
        issue_dates.setdefault(client_id, []).append(issue_date)
    fit = fit_bgnbd(issue_dates, _today(db, source))
    return fit.get("probabilities", {})


def _drift_for(db: Session, client: Client, activity_probability: float | None = None) -> dict:
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
    client.drift = drift
    client.scored_at = datetime.now(timezone.utc)
    return drift


def _last_attempt(db: Session, invoice_id: str) -> tuple[datetime | None, bool, str | None, datetime | None]:
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
    return db.scalar(select(Recommendation).where(Recommendation.invoice_id == invoice_id)
                     .order_by(Recommendation.created_at.desc()).limit(1))


def _client_has_partial_history(db: Session, client_id: str) -> bool:
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
    partial_invoice_count = sum(count > 1 for _, count in payment_counts)
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


def _invoice_dict(
    db: Session,
    invoice: Invoice,
    include_detail: bool = False,
    drift_cache: dict[str, dict] | None = None,
    activity_scores: dict[str, float] | None = None,
    daily_candidate_ids: set[str] | None = None,
    forecast_cache: dict[str, dict] | None = None,
    create_recommendation: bool = True,
) -> dict:
    client = invoice.client or db.get(Client, invoice.client_id)
    today = _today(db, invoice.source)
    outstanding = Decimal("0.00") if invoice.status in CLOSED_STATUSES else invoice.outstanding
    forecast = None
    if invoice.status not in CLOSED_STATUSES and invoice.outstanding > 0:
        forecast = None if include_detail else (forecast_cache or {}).get(invoice.id)
        if forecast is None:
            forecast = forecast_invoice(db, invoice, today=today, persist=include_detail)
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
    if invoice.disputed or (invoice.promised_payment_date and today <= invoice.promised_payment_date):
        recommendation = None
    if include_detail and create_recommendation and (not recommendation or recommendation.invoice_version != invoice.material_version or recommendation.state in {"skipped", "sent", "failed"}) and action["action"] in {"friendly", "firm"}:
        provider_url = ((invoice.provider_payload.get("detail") or {}).get("metadata") or {}).get("recipient_view_url")
        if not isinstance(provider_url, str) or urlparse(provider_url).scheme != "https" or not urlparse(provider_url).hostname or not urlparse(provider_url).hostname.endswith("paypal.com"):
            provider_url = None
        body = render_message(client.display_name, invoice.outstanding, invoice.currency, invoice.due_date, action["tone"], provider_url)
        recommendation = Recommendation(
            invoice_id=invoice.id, action=action["action"], tone=action["tone"], reason=action["reason"],
            draft_text=body, draft_source="template", edited_text=body, invoice_version=invoice.material_version,
            policy_step=action.get("policy_step"),
        )
        db.add(recommendation)
        db.flush()
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
        "source": invoice.source, "material_version": invoice.material_version,
        "synced_at": invoice.synced_at.isoformat() if invoice.synced_at else None,
        "viewed_at": invoice.viewed_at.isoformat() if invoice.viewed_at else None,
        "partial_payment_allowed": invoice.partial_payment_allowed,
        "payment_term_type": invoice.payment_term_type,
        "reminder_events": invoice.reminder_events or [],
        "payment_url": (((invoice.provider_payload.get("detail") or {}).get("metadata") or {}).get("recipient_view_url")) if invoice.source == "paypal" else None,
        "forecast": forecast, "drift": drift, "decision": action,
        "why": _why_line(forecast, drift),
        "recommendation": _recommendation_dict(recommendation),
        "days_until_due_nonnegative": currency_due,
    }
    if include_detail:
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
    query = select(Invoice).order_by(Invoice.due_date.asc())
    if source:
        query = query.where(Invoice.source == source)
    rows = list(db.scalars(query).all())
    candidates = []
    for invoice in rows:
        if not _payable(invoice):
            continue
        invoice_today = _today(db, invoice.source)
        forecast = (forecast_cache or {}).get(invoice.id)
        if forecast is None:
            forecast = forecast_invoice(db, invoice, today=invoice_today, persist=False)
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
    if db.scalar(select(PolicyDecisionLog.id).where(PolicyDecisionLog.fingerprint == fingerprint)):
        return
    options = ["friendly", "firm", "offer_partial_payment", "personal_call", "wait"]
    alternatives = [{"action": name, "selected": name == decision["action"]} for name in options]
    db.add(PolicyDecisionLog(
        invoice_id=invoice.id, decision_date=date.fromisoformat(fields["decision_date"]), fingerprint=fingerprint,
        features=fields, chosen_action=decision["action"], alternatives=alternatives,
        assigned_holdback=bool(decision.get("holdback")), outcome={"status": "pending"},
    ))


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
        payload["feature_importance"] = json.loads(importance_path.read_text()).get("features", [])
    except (OSError, ValueError, AttributeError):
        payload["feature_importance"] = []
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
    action_order = {"friendly": 0, "firm": 1, "offer_partial_payment": 2, "personal_call": 3, "wait": 4}

    def key(item: dict):
        decision = item["decision"]
        if sort == "action":
            rank = decision.get("daily_rank")
            return (rank is None, rank or 10_000, action_order.get(decision["action"], 5),
                    -decision.get("expected_cash_gain", 0), item["due_date"], item["invoice_number"])
        if sort == "expected_cash":
            return (decision.get("expected_cash_gain", 0), item["due_date"], item["invoice_number"])
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


@app.get("/invoices")
def invoices(
    request: Request,
    q: str | None = None,
    limit: int | None = None,
    page: int = 1,
    page_size: int = 25,
    view: str = "open",
    client_id: str | None = None,
    age_bucket: str | None = None,
    sort: str = "action",
    direction: str = "asc",
    action_only: bool = False,
    drifting_only: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    user = session_user(request)
    page = max(1, page)
    if limit is not None and page_size == 25:
        page_size = limit
    page_size = min((25, 50, 100), key=lambda size: abs(size - page_size))
    allowed_views = {"open", "due_this_week", "overdue", "paid", "all"}
    view = view if view in allowed_views else "open"
    allowed_buckets = {"not_due", "late_1_30", "late_31_60", "late_61_90", "late_90_plus"}
    age_bucket = age_bucket if age_bucket in allowed_buckets else None
    allowed_sorts = {"action", "expected_cash", "p14", "due", "client", "balance", "invoice", "status"}
    sort = sort if sort in allowed_sorts else "action"
    direction = "desc" if direction.casefold() == "desc" else "asc"

    query = select(Invoice).options(joinedload(Invoice.client)).order_by(Invoice.due_date.asc(), Invoice.invoice_number.asc())
    if not user:
        query = query.where(Invoice.source == "demo")
    base_rows = list(db.scalars(query).unique().all())
    if client_id:
        base_rows = [row for row in base_rows if row.client_id == client_id]
    if q:
        needle = q.casefold()
        base_rows = [row for row in base_rows if any(needle in value.casefold() for value in (
            row.invoice_number, row.client.display_name, row.status, row.client.email or "",
        ))]

    source_names = ["demo"] if not user else ["demo", "paypal"]
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

    view_counts = {name: 0 for name in ("open", "due_this_week", "overdue", "paid", "all")}
    ready_recommendations = set()
    for source in source_names:
        today = _today(db, source)
        ready_recommendations.update(db.scalars(select(Recommendation.invoice_id).join(Invoice).where(
            Invoice.source == source, Recommendation.state == "proposed",
            Recommendation.action.in_(["friendly", "firm"]),
            func.date(Recommendation.created_at) == today.isoformat(),
        )).all())
    for row in base_rows:
        open_invoice = _payable(row)
        today = _today(db, row.source)
        view_counts["all"] += 1
        view_counts["open"] += int(open_invoice)
        view_counts["due_this_week"] += int(open_invoice and 0 <= (row.due_date - today).days <= 7)
        view_counts["overdue"] += int(open_invoice and row.due_date < today)
        view_counts["paid"] += int(row.status == "PAID")

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

    items = [_invoice_dict(
        db, row, drift_cache=drift_cache, activity_scores=activity_by_source.get(row.source),
        daily_candidate_ids=daily_candidate_ids, forecast_cache=forecast_cache,
    ) for row in rows]
    for item in items:
        if item["id"] in candidate_ranks:
            item["decision"]["daily_rank"] = candidate_ranks[item["id"]]
    if action_only:
        items = [item for item in items if item["id"] in ready_recommendations]
    _sort_invoice_items(items, sort, direction)

    totals: dict[str, Decimal] = {}
    for item in items:
        if Decimal(item["outstanding"]) > 0 and item["status"] not in CLOSED_STATUSES:
            totals[item["currency"]] = totals.get(item["currency"], Decimal("0")) + Decimal(item["outstanding"])
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
        forecast = forecast_cache.get(row.id) or forecast_invoice(db, row, today=_today(db, row.source), persist=False)
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
        entry["amount"] += Decimal(row.outstanding)
        entry["invoice_count"] += 1

    filtered_count = len(items)
    page_count = max(1, (filtered_count + page_size - 1) // page_size)
    page_items = items[(page - 1) * page_size:page * page_size]
    rows_by_id = {row.id: row for row in rows}
    for item in page_items:
        _record_policy_decision(db, rows_by_id[item["id"]], item)
    model_names = sorted({item["forecast"]["model"] for item in page_items if item["forecast"]})
    ready_for_review_count = len(ready_recommendations)
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
    db.commit()
    return {
        "recommendation": _recommendation_dict(recommendation),
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
def clients(request: Request, db: Session = Depends(get_db)) -> dict:
    user = session_user(request)
    query = select(Client).order_by(Client.display_name.asc())
    if not user:
        query = query.where(Client.source == "demo")
    rows = list(db.scalars(query).all())
    activity_by_source = {source: _activity_for_source(db, source) for source in {row.source for row in rows}}
    output = []
    for client in rows:
        count = db.scalar(select(func.count(Invoice.id)).where(Invoice.client_id == client.id)) or 0
        output.append({"id": client.id, "name": client.display_name, "source": client.source,
                       "invoice_count": count, "drift": _drift_for(db, client, activity_by_source[client.source].get(client.id))})
    output.sort(key=lambda row: (-row["drift"].get("severity_score", 0), row["name"].casefold()))
    counts = {name: sum(row["drift"].get("signal_type") == name for row in output)
              for name in ("paying_later", "later_than_usual", "gone_quiet", "insufficient_history")}
    return {"items": output, "signal_counts": counts}


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
    if not client or (client.source != "demo" and not session_user(request)):
        raise HTTPException(status_code=404, detail="Client not found.")
    query = select(Invoice).options(joinedload(Invoice.client)).where(Invoice.client_id == client.id).order_by(Invoice.issue_date.desc())
    rows = list(db.scalars(query).all())
    history = _client_history_facts(db, client.id)
    activity = _activity_for_source(db, client.source)
    drift = _drift_for(db, client, activity.get(client.id))
    cache: dict[str, dict] = {}
    candidates = _top_policy_candidates(db, client.source)
    invoice_facts = []
    for row in rows:
        item = _invoice_dict(db, row, drift_cache={client.id: drift}, activity_scores=activity,
                             daily_candidate_ids=candidates, forecast_cache=cache, create_recommendation=False)
        invoice_facts.append({key: item[key] for key in (
            "id", "invoice_number", "issue_date", "due_date", "days_to_due", "total", "paid", "outstanding",
            "currency", "status", "forecast", "why", "decision",
        )})
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
        return {"intent": "largest_client_balance", "answer": answer, "data": data}

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
        return {"intent": "clients_slowing_down", "answer": answer, "data": signals[:5]}

    if re.search(r"\b(cash|payments?|settlements?)\b", lowered) and re.search(r"\b(this week|next 7 days)\b", lowered):
        totals: dict[str, Decimal] = {}
        counts: dict[str, int] = {}
        for row in rows:
            if not _payable(row):
                continue
            today = _today(db, row.source)
            forecast = forecast_invoice(db, row, today=today, persist=False)
            p50 = forecast.get("quantiles", {}).get("p50") or {}
            if p50.get("date"):
                expected = date.fromisoformat(p50["date"])
                if today <= expected <= today + timedelta(days=7):
                    totals[row.currency] = totals.get(row.currency, Decimal("0")) + row.outstanding
                    counts[row.currency] = counts.get(row.currency, 0) + 1
        data = [{"currency": key, "amount": str(value), "invoice_count": counts[key]} for key, value in sorted(totals.items())]
        answer = "Expected p50 settlement balances in the next week: " + (", ".join(f"{item['currency']} {Decimal(item['amount']):,.2f}" for item in data) if data else "none.")
        return {"intent": "expected_cash_this_week", "answer": answer, "data": data}

    if "overdue" in lowered:
        overdue = [row for row in rows if _payable(row) and row.due_date < _today(db, row.source) and (not currency or row.currency == currency)]
        overdue.sort(key=lambda row: (-row.outstanding, row.due_date))
        data = [{"invoice_id": row.id, "invoice_number": row.invoice_number, "client_name": row.client.display_name,
                 "currency": row.currency, "outstanding": str(row.outstanding), "due_date": row.due_date.isoformat()}
                for row in overdue[:5]]
        answer = f"There are {len(overdue)} overdue open invoices" + (f" in {currency}" if currency else "") + "."
        return {"intent": "overdue_invoices", "answer": answer, "data": data}

    return {
        "intent": "unsupported", "answer": "I can answer largest open balance by currency, clients with timing signals, expected cash this week, or overdue invoices.",
        "data": None,
    }


@app.post("/sync")
def sync(request: Request, db: Session = Depends(get_db)) -> dict:
    require_operator(request)
    try:
        return sync_paypal(db, PayPalAdapter())
    except PayPalError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
    event.processing_status = "received"
    event.failure_reason = None
    db.commit()
    try:
        paypal_id = webhook_invoice_id(payload)
        if not paypal_id:
            raise ValueError("Webhook resource did not include an invoice ID.")
        refresh_paypal_invoice(db, PayPalAdapter(), str(paypal_id))
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
    if recommendation.policy_step is not None:
        invoice.reminder_step = max(invoice.reminder_step, recommendation.policy_step + 1)
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
    if state == "sent" and recommendation.policy_step is not None:
        invoice = db.get(Invoice, recommendation.invoice_id)
        if invoice:
            invoice.reminder_step = max(invoice.reminder_step, recommendation.policy_step + 1)
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


@app.post("/demo/recommendations/{recommendation_id}/send")
def demo_send(recommendation_id: str, payload: SendInput, db: Session = Depends(get_db)) -> dict:
    recommendation = db.get(Recommendation, recommendation_id)
    if not recommendation:
        raise HTTPException(status_code=404, detail="Recommendation not found.")
    invoice = _get_invoice_or_404(db, recommendation.invoice_id)
    if invoice.source != "demo":
        raise HTTPException(status_code=403, detail="Demo simulation is restricted to synthetic invoices.")
    if recommendation.invoice_version != invoice.material_version or not _payable(invoice):
        raise HTTPException(status_code=409, detail="Invoice changed or is no longer payable.")
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
