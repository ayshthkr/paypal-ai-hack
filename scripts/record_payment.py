from __future__ import annotations

import argparse
import sys
from decimal import Decimal, InvalidOperation

from app.config import get_settings
from app.db import SessionLocal
from app.services.paypal import PayPalAdapter, PayPalError, normalize_invoice
from app.services.sync import upsert_paypal_invoice
from app.services.policy import CLOSED_STATUSES


METHODS = ("BANK_TRANSFER", "CASH", "CHECK", "CREDIT_CARD", "DEBIT_CARD", "PAYPAL", "WIRE_TRANSFER", "OTHER")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Record an externally completed payment on an allowlisted PayPal Sandbox invoice.")
    result.add_argument("--invoice-id", required=True, help="PayPal invoice ID")
    result.add_argument("--amount", required=True, type=Decimal, help="Payment amount, no greater than the current balance")
    result.add_argument("--currency", required=True, help="Three-letter invoice currency")
    result.add_argument("--method", choices=METHODS, default="BANK_TRANSFER")
    result.add_argument("--note", default="", help="Optional payment note (up to 2000 characters)")
    return result


def main() -> int:
    args = parser().parse_args()
    settings = get_settings()
    if settings.paypal_mode.lower() != "sandbox":
        print("Error: payment recording is restricted to PAYPAL_MODE=sandbox.", file=sys.stderr)
        return 2
    if args.amount <= 0:
        print("Error: --amount must be greater than zero.", file=sys.stderr)
        return 2
    if len(args.currency) != 3 or not args.currency.isalpha():
        print("Error: --currency must be a three-letter ISO 4217 code.", file=sys.stderr)
        return 2

    adapter = PayPalAdapter(settings)
    try:
        provider_invoice = adapter.get_invoice(args.invoice_id)
        invoice = normalize_invoice(provider_invoice)
        if str(provider_invoice.get("id")) != args.invoice_id:
            raise PayPalError("PayPal returned a different invoice ID.")
        if (invoice["email"] or "").strip().lower() not in settings.allowed_recipients:
            raise PayPalError("Invoice recipient is not in PAYPAL_ALLOWED_RECIPIENT_EMAILS.")
        if invoice["status"] in CLOSED_STATUSES or invoice["outstanding"] <= 0:
            raise PayPalError("Invoice is closed or has no outstanding balance.")
        if args.currency.upper() != invoice["currency"]:
            raise PayPalError(f"Currency mismatch: invoice uses {invoice['currency']}.")
        if args.amount > invoice["outstanding"]:
            raise PayPalError("Payment amount cannot exceed the current invoice balance.")

        adapter.record_payment(args.invoice_id, args.amount, invoice["currency"], args.method, args.note)
    except PayPalError as exc:
        if exc.ambiguous:
            print(f"PayPal delivery is uncertain: {exc} Run `make sync` before considering another payment record.", file=sys.stderr)
        else:
            print(f"PayPal payment recording failed: {exc}", file=sys.stderr)
        return 1

    try:
        refreshed = adapter.get_invoice(args.invoice_id)
        db = SessionLocal()
        try:
            updated, _ = upsert_paypal_invoice(db, refreshed)
            db.commit()
            print(f"PayPal invoice {args.invoice_id} refreshed: {updated.status}, {updated.currency} {updated.outstanding} outstanding.")
        finally:
            db.close()
    except Exception as exc:
        print(f"PayPal accepted the payment record, but local refresh failed ({exc}). Run `make sync`; do not record it again.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InvalidOperation:
        print("Error: --amount must be a valid decimal value.", file=sys.stderr)
        raise SystemExit(2)
