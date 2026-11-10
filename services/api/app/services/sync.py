from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Client, Invoice, InvoiceEvent, Payment
from app.services.drift import client_drift
from app.services.forecast import _as_record
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
            synced_at=now, material_version=1,
        )
        db.add(invoice)
        db.flush()
    else:
        material = (invoice.invoice_number, invoice.client_id, invoice.issue_date, invoice.due_date,
                    invoice.total, invoice.paid, invoice.outstanding, invoice.currency, invoice.status)
        updated_material = (values["invoice_number"], client.id, values["issue_date"], values["due_date"],
                            values["total"], values["paid"], values["outstanding"], values["currency"], values["status"])
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
    return invoice


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
    created = 0
    updated = 0
    failures: list[str] = []
    for item in adapter.list_invoices():
        paypal_id = item.get("id")
        if not paypal_id:
            failures.append("PayPal returned an invoice without an ID.")
            continue
        try:
            payload = adapter.get_invoice(str(paypal_id))
            _, is_created = upsert_paypal_invoice(db, payload)
            created += int(is_created)
            updated += int(not is_created)
        except Exception as exc:
            failures.append(f"Invoice {paypal_id}: {str(exc)[:300]}")
    retried, retry_failed = retry_event_refreshes(db, adapter)
    db.commit()
    return {"created": created, "updated": updated, "event_refreshes": retried,
            "retry_failures": retry_failed, "errors": failures}
