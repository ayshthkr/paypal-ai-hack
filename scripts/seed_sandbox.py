from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from app.config import get_settings
from app.services.paypal import PayPalAdapter


def seed() -> list[dict[str, str]]:
    settings = get_settings()
    if settings.paypal_mode.lower() != "sandbox":
        raise RuntimeError("Sandbox seeding is only enabled when PAYPAL_MODE=sandbox.")
    if not settings.allowed_recipients:
        raise RuntimeError("Set PAYPAL_ALLOWED_RECIPIENT_EMAILS to controlled Sandbox buyer addresses first.")
    adapter = PayPalAdapter(settings)
    currency = settings.paypal_sandbox_currency.upper()
    created = []
    for index, email in enumerate(sorted(settings.allowed_recipients)[:3], start=1):
        invoice_number = f"LP-SBX-{date.today():%y%m%d}-{uuid4().hex[:6].upper()}"
        invoice = adapter.create_invoice(email, Decimal("49.00") + Decimal(index), currency, invoice_number,
                                         date.today() + timedelta(days=1), "Controlled Late Payer Sandbox fixture.")
        paypal_id = str(invoice.get("id") or "")
        if not paypal_id:
            raise RuntimeError("PayPal created an invoice without returning its ID.")
        adapter.send_invoice(paypal_id)
        created.append({"id": paypal_id, "invoice_number": invoice_number, "recipient": email})
    return created


if __name__ == "__main__":
    print(seed())
