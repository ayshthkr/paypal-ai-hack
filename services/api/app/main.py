from __future__ import annotations

import hashlib
import hmac
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import get_db
from app.models import ActionAttempt, Client, DemoState, Invoice, InvoiceEvent, Payment, Recommendation
from app.security import COOKIE_NAME, create_session, require_operator, session_user
from app.services.drift import client_drift
from app.services.forecast import _as_record, forecast_invoice
from app.services.paypal import PayPalAdapter, PayPalError, webhook_invoice_id
from app.services.policy import CLOSED_STATUSES, decide, render_message
from app.services.sync import refresh_paypal_invoice, sync_paypal

settings = get_settings()
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


def _drift_for(db: Session, client: Client) -> dict:
    cadence = None
    records = _client_records(db, client.id)
    issued = sorted(row["issue_date"] for row in records)
    if len(issued) >= 5:
        gaps = [(issued[index] - issued[index - 1]).days for index in range(1, len(issued))]
        cadence = float(sorted(gaps)[len(gaps) // 2])
    drift = client_drift(records, _today(db, client.source), cadence_days=cadence)
    client.drift = drift
    client.scored_at = datetime.now(timezone.utc)
    return drift


def _last_attempt(db: Session, invoice_id: str) -> tuple[datetime | None, bool]:
    attempts = list(db.scalars(
        select(ActionAttempt).join(Recommendation).where(Recommendation.invoice_id == invoice_id)
        .order_by(ActionAttempt.started_at.desc()).limit(10)
    ).all())
    last = next((attempt.started_at for attempt in attempts if attempt.state in {"sent", "sending", "unknown"}), None)
    unresolved = any(attempt.state in {"sending", "unknown"} for attempt in attempts)
    return last, unresolved


def _latest_recommendation(db: Session, invoice_id: str) -> Recommendation | None:
    return db.scalar(select(Recommendation).where(Recommendation.invoice_id == invoice_id)
                     .order_by(Recommendation.created_at.desc()).limit(1))


def _invoice_dict(db: Session, invoice: Invoice, include_detail: bool = False, drift_cache: dict[str, dict] | None = None) -> dict:
    client = invoice.client or db.get(Client, invoice.client_id)
    today = _today(db, invoice.source)
    forecast = None
    if invoice.status not in CLOSED_STATUSES and invoice.outstanding > 0:
        forecast = forecast_invoice(db, invoice, today=today, persist=include_detail)
    if drift_cache is not None and client.id in drift_cache:
        drift = drift_cache[client.id]
    else:
        drift = _drift_for(db, client)
        if drift_cache is not None:
            drift_cache[client.id] = drift
    last_attempt, unresolved = _last_attempt(db, invoice.id) if _payable(invoice) else (None, False)
    action = decide(invoice, forecast["probability_by_due"] if forecast else None, last_attempt, unresolved, today)
    recommendation = _latest_recommendation(db, invoice.id)
    if include_detail and (not recommendation or recommendation.invoice_version != invoice.material_version or recommendation.state in {"skipped", "sent", "failed"}) and action["action"] in {"friendly", "firm"}:
        provider_url = ((invoice.provider_payload.get("detail") or {}).get("metadata") or {}).get("recipient_view_url")
        if not isinstance(provider_url, str) or urlparse(provider_url).scheme != "https" or not urlparse(provider_url).hostname or not urlparse(provider_url).hostname.endswith("paypal.com"):
            provider_url = None
        body = render_message(client.display_name, invoice.outstanding, invoice.currency, invoice.due_date, action["tone"], provider_url)
        recommendation = Recommendation(
            invoice_id=invoice.id, action=action["action"], tone=action["tone"], reason=action["reason"],
            draft_text=body, edited_text=body, invoice_version=invoice.material_version,
        )
        db.add(recommendation)
        db.flush()
    currency_due = max(0, (invoice.due_date - today).days)
    summary = {
        "id": invoice.id, "paypal_id": invoice.paypal_id, "invoice_number": invoice.invoice_number,
        "client_id": client.id, "client_name": client.display_name, "client_email": client.email,
        "issue_date": invoice.issue_date.isoformat(), "due_date": invoice.due_date.isoformat(),
        "days_to_due": (invoice.due_date - today).days,
        "total": str(invoice.total), "paid": str(invoice.paid), "outstanding": str(invoice.outstanding),
        "currency": invoice.currency, "status": invoice.status, "disputed": invoice.disputed,
        "source": invoice.source, "material_version": invoice.material_version,
        "synced_at": invoice.synced_at.isoformat() if invoice.synced_at else None,
        "viewed_at": invoice.viewed_at.isoformat() if invoice.viewed_at else None,
        "partial_payment_allowed": invoice.partial_payment_allowed,
        "payment_term_type": invoice.payment_term_type,
        "reminder_events": invoice.reminder_events or [],
        "payment_url": (((invoice.provider_payload.get("detail") or {}).get("metadata") or {}).get("recipient_view_url")) if invoice.source == "paypal" else None,
        "forecast": forecast, "drift": drift, "decision": action,
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
        "edited_text": recommendation.edited_text, "invoice_version": recommendation.invoice_version,
        "state": recommendation.state, "approved_text": recommendation.approved_text,
        "approved_at": recommendation.approved_at.isoformat() if recommendation.approved_at else None,
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


def _check_policy(db: Session, invoice: Invoice, forecast: dict | None) -> None:
    last_attempt, unresolved = _last_attempt(db, invoice.id)
    action = decide(invoice, forecast["probability_by_due"] if forecast else None, last_attempt, unresolved, _today(db, invoice.source))
    if action["action"] not in {"friendly", "firm"}:
        raise HTTPException(status_code=409, detail=action["reason"])


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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


@app.get("/invoices")
def invoices(request: Request, q: str | None = None, limit: int = 200, db: Session = Depends(get_db)) -> dict:
    user = session_user(request)
    priority = case((Invoice.status.in_(CLOSED_STATUSES), 1), else_=0)
    query = select(Invoice).options(joinedload(Invoice.client)).order_by(priority.asc(), Invoice.due_date.asc(), Invoice.invoice_number.asc()).limit(min(max(limit, 1), 500))
    if not user:
        query = query.where(Invoice.source == "demo")
    rows = list(db.scalars(query).unique().all())
    if q:
        needle = q.casefold()
        rows = [row for row in rows if needle in row.invoice_number.casefold() or needle in row.client.display_name.casefold() or needle in row.status.casefold()]
    drift_cache: dict[str, dict] = {}
    items = [_invoice_dict(db, row, drift_cache=drift_cache) for row in rows]
    totals: dict[str, Decimal] = {}
    for row in rows:
        if _payable(row):
            totals[row.currency] = totals.get(row.currency, Decimal("0")) + Decimal(row.outstanding)
    model_names = sorted({item["forecast"]["model"] for item in items if item["forecast"]})
    return {"items": items, "count": len(items), "currency_totals": {key: str(value) for key, value in sorted(totals.items())},
            "models": model_names, "synthetic_demo": not user}


@app.get("/invoices/{invoice_id}")
def invoice_detail(invoice_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    invoice = _get_invoice_or_404(db, invoice_id)
    _authorize_invoice(request, invoice)
    payload = _invoice_dict(db, invoice, include_detail=True)
    db.commit()
    return payload


@app.get("/clients")
def clients(request: Request, db: Session = Depends(get_db)) -> dict:
    user = session_user(request)
    query = select(Client).order_by(Client.display_name.asc())
    if not user:
        query = query.where(Client.source == "demo")
    rows = list(db.scalars(query).all())
    output = []
    for client in rows:
        count = db.scalar(select(func.count(Invoice.id)).where(Invoice.client_id == client.id)) or 0
        output.append({"id": client.id, "name": client.display_name, "source": client.source,
                       "invoice_count": count, "drift": _drift_for(db, client)})
    return {"items": output}


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
    _check_policy(db, invoice, forecast_invoice(db, invoice, today=_today(db, invoice.source), persist=False))
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
    client = db.get(Client, invoice.client_id)
    _drift_for(db, client)
    db.commit()
    return {"invoice_id": invoice.id, "status": invoice.status, "paid": str(invoice.paid),
            "outstanding": str(invoice.outstanding), "currency": invoice.currency,
            "simulated": True, "note": "Synthetic demo only; no PayPal payment was made."}
