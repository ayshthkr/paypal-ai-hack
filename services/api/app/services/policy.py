from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.config import get_settings

CLOSED_STATUSES = {"PAID", "CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"}


def decide(invoice, probability_by_due: float | None, last_attempt_at=None, unresolved=False, today: date | None = None) -> dict:
    settings = get_settings()
    today = today or date.today()
    if invoice.disputed or invoice.status in CLOSED_STATUSES or Decimal(str(invoice.outstanding)) <= 0:
        return {"action": "wait", "tone": "none", "reason": "Invoice is closed, disputed, or has no outstanding balance."}
    if last_attempt_at and (today - last_attempt_at.date()).days < settings.reminder_cooldown_days:
        return {"action": "wait", "tone": "none", "reason": f"A reminder was attempted within the last {settings.reminder_cooldown_days} days."}
    if unresolved:
        return {"action": "wait", "tone": "none", "reason": "A previous delivery is unresolved; reconcile it before another reminder."}
    if invoice.due_date < today:
        return {"action": "firm", "tone": "firm", "reason": "Invoice is overdue and has an outstanding balance."}
    days_until_due = (invoice.due_date - today).days
    if days_until_due <= settings.reminder_days_before_due:
        threshold = settings.friendly_payment_probability_threshold
        if probability_by_due is not None and probability_by_due < threshold:
            return {"action": "friendly", "tone": "friendly", "reason": f"Due within {settings.reminder_days_before_due} days and predicted payment probability by due date is below {threshold:.0%}."}
        return {"action": "wait", "tone": "none", "reason": "Due soon, but predicted payment probability by the due date is at or above the reminder threshold."}
    return {"action": "wait", "tone": "none", "reason": "No reminder before three days prior to the due date."}


def render_message(client_name: str, balance: Decimal, currency: str, due_date: date, tone: str, payment_url: str | None = None) -> str:
    greeting = f"Hello {client_name},"
    balance_text = f"{currency} {balance:,.2f}"
    link = f"\nPay securely: {payment_url}" if payment_url else ""
    if tone == "firm":
        return f"{greeting}\nOur records show {balance_text} remains outstanding on your invoice due {due_date.isoformat()}. Please arrange payment or contact us if you have questions.{link}\nThank you."
    return f"{greeting}\nA friendly reminder that {balance_text} is due on {due_date.isoformat()}. Please let us know if you need anything.{link}\nThank you."
