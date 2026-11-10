from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Client, Invoice, InvoiceEvent, Payment, SyncRun
from app.services.drift import client_drift
from app.services.forecast import _as_record, forecast_invoice
from app.services.paypal import PayPalAdapter, payment_records, normalize_invoice, webhook_invoice_id


def upsert_paypal_invoice(db: Session, payload: dict) -> tuple[Invoice, bool]:
    values = normalize_invoice(payload)
    client = db.scalar(select(Client).where(Client.provider_customer_id == values["client_identity"]))
    if client is None:
        client = Client(
            provider_customer_id=values["client_identity"], display_name=values["client_name"],
            email=values["email"], source="paypal",
        )
        db.add(client)
        db.flush()
    invoice = db.scalar(select(Invoice).where(Invoice.paypal_id == values["paypal_id"]))
    created = invoice is None
    now = datetime.now(timezone.utc)
    if invoice is None:
        invoice = Invoice(
            paypal_id=values["paypal_id"], invoice_number=values["invoice_number"], client_id=client.id,
            issue_date=values["issue_date"], due_date=values["due_date"], total=values["total"],
            paid=values["paid"], outstanding=values["outstanding"], currency=values["currency"],
            status=values["status"], source="paypal", provider_payload=values["payload"],
            viewed_at=values["viewed_at"], viewed_by_recipient=values["viewed_by_recipient"] is True,
            viewed_observed_at=now if values["viewed_by_recipient"] is True and not values["viewed_at"] else None,
            partial_payment_allowed=values["partial_payment_allowed"], payment_term_type=values["payment_term_type"],
            synced_at=now, material_version=1,
        )
        db.add(invoice)
        db.flush()
    else:
        material = (invoice.invoice_number, invoice.client_id, invoice.issue_date, invoice.due_date,
                    invoice.total, invoice.paid, invoice.outstanding, invoice.currency, invoice.status,
                    invoice.viewed_at, invoice.viewed_by_recipient, invoice.partial_payment_allowed,
                    invoice.payment_term_type, tuple((invoice.reminder_events or [])))
        updated_material = (values["invoice_number"], client.id, values["issue_date"], values["due_date"],
                            values["total"], values["paid"], values["outstanding"], values["currency"], values["status"],
                            values["viewed_at"] or invoice.viewed_at,
                            values["viewed_by_recipient"] if values["viewed_by_recipient"] is not None else invoice.viewed_by_recipient,
                            values["partial_payment_allowed"], values["payment_term_type"], tuple(invoice.reminder_events or []))
        if material != updated_material:
            invoice.material_version += 1
        invoice.invoice_number = values["invoice_number"]
        invoice.client_id = client.id
        invoice.issue_date = values["issue_date"]
        invoice.due_date = values["due_date"]
        invoice.total = values["total"]
        invoice.paid = values["paid"]
        invoice.outstanding = values["outstanding"]
        invoice.currency = values["currency"]
        invoice.status = values["status"]
        if values["viewed_by_recipient"] is not None:
            invoice.viewed_by_recipient = values["viewed_by_recipient"]
        if values["viewed_at"]:
            invoice.viewed_at = values["viewed_at"]
        if invoice.viewed_by_recipient and not invoice.viewed_at and not invoice.viewed_observed_at:
            invoice.viewed_observed_at = now
        invoice.partial_payment_allowed = values["partial_payment_allowed"]
        invoice.payment_term_type = values["payment_term_type"]
        invoice.provider_payload = values["payload"]
        invoice.synced_at = now
        db.flush()
    for record in payment_records(payload):
        exists = db.scalar(select(Payment.id).where(Payment.provider_payment_id == record["provider_payment_id"]))
        if not exists:
            db.add(Payment(
                provider_payment_id=record["provider_payment_id"], invoice_id=invoice.id,
                amount=record["amount"], currency=record["currency"], occurred_at=record["occurred_at"], source="paypal",
            ))
    db.flush()
    client_invoices = list(db.scalars(select(Invoice).where(Invoice.client_id == client.id)).all())
    invoice_ids = [item.id for item in client_invoices]
    all_payments = list(db.scalars(select(Payment).where(Payment.invoice_id.in_(invoice_ids))).all()) if invoice_ids else []
    payments_by_invoice: dict[str, list[Payment]] = {}
    for payment in all_payments:
        payments_by_invoice.setdefault(payment.invoice_id, []).append(payment)
    records = [_as_record(item, payments_by_invoice.get(item.id, [])) for item in client_invoices]
    client.drift = client_drift(records, datetime.now(timezone.utc).date())
    client.scored_at = now
    db.flush()
    return invoice, created


def refresh_paypal_invoice(db: Session, adapter: PayPalAdapter, paypal_id: str) -> Invoice:
    payload = adapter.get_invoice(paypal_id)
    invoice, _ = upsert_paypal_invoice(db, payload)
    rescore_paypal_client(db, invoice.client_id)
    return invoice


def rescore_paypal_client(db: Session, client_id: str) -> None:
    """Refresh persisted forecasts after a provider invoice event changes client facts."""
    today = datetime.now(timezone.utc).date()
    rows = list(db.scalars(select(Invoice).where(Invoice.client_id == client_id, Invoice.source == "paypal")).all())
    for invoice in rows:
        if invoice.status not in {"PAID", "CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"} and invoice.outstanding > 0:
            forecast_invoice(db, invoice, today=today, persist=True)


def retry_event_refreshes(db: Session, adapter: PayPalAdapter) -> tuple[int, int]:
    events = list(db.scalars(select(InvoiceEvent).where(
        InvoiceEvent.verified.is_(True), InvoiceEvent.processing_status.in_(["received", "retry"]),
    ).order_by(InvoiceEvent.received_at).limit(100)).all())
    succeeded = 0
    failed = 0
    for event in events:
        try:
            paypal_id = webhook_invoice_id(event.raw_payload)
            if not paypal_id:
                raise ValueError("Webhook resource did not include an invoice ID.")
            refresh_paypal_invoice(db, adapter, str(paypal_id))
            event.processing_status = "processed"
            event.failure_reason = None
            succeeded += 1
        except Exception as exc:  # persisted for the next explicit sync
            event.processing_status = "retry"
            event.failure_reason = str(exc)[:1000]
            failed += 1
    db.flush()
    return succeeded, failed


def sync_paypal(db: Session, adapter: PayPalAdapter) -> dict:
    run = SyncRun(status="running")
    db.add(run)
    db.flush()
    created = 0
    updated = 0
    failures: list[str] = []
    touched_clients: set[str] = set()
    try:
        listed_invoices = adapter.list_invoices()
    except Exception as exc:
        listed_invoices = []
        failures.append(f"Could not list PayPal invoices: {str(exc)[:300]}")
    for item in listed_invoices:
        paypal_id = item.get("id")
        if not paypal_id:
            failures.append("PayPal returned an invoice without an ID.")
            continue
        try:
            payload = adapter.get_invoice(str(paypal_id))
            invoice, is_created = upsert_paypal_invoice(db, payload)
            created += int(is_created)
            updated += int(not is_created)
            touched_clients.add(invoice.client_id)
        except Exception as exc:
            failures.append(f"Invoice {paypal_id}: {str(exc)[:300]}")
    retried, retry_failed = retry_event_refreshes(db, adapter)
    for client_id in touched_clients:
        rescore_paypal_client(db, client_id)
    run.completed_at = datetime.now(timezone.utc)
    run.status = "partial" if failures or retry_failed else "succeeded"
    run.created_count = created
    run.updated_count = updated
    run.event_refreshes = retried
    run.retry_failures = retry_failed
    run.errors = failures
    db.commit()
    return {"run_id": run.id, "status": run.status, "completed_at": run.completed_at.isoformat(),
            "created": created, "updated": updated, "event_refreshes": retried,
            "retry_failures": retry_failed, "errors": failures}
