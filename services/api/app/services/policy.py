from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

from app.config import get_settings

CLOSED_STATUSES = {"PAID", "CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"}
DAILY_SUGGESTION_CAP = 5
REMINDER_UPLIFT_ASSUMPTION = 0.10


def _bounded_probability(value: float | None) -> float:
    return min(1.0, max(0.0, float(value or 0.0)))


def _max_stage(days_late: int, days_until_due: int) -> int:
    if days_late >= 45:
        return 4
    if days_late >= 21:
        return 3
    if days_late >= 10:
        return 2
    if days_late >= 3:
        return 1
    if 0 <= days_until_due <= 3:
        return 0
    return -1


def decide(
    invoice,
    probability_by_due: float | None,
    last_attempt_at=None,
    unresolved: bool = False,
    today: date | None = None,
    probability_14d: float | None = None,
    last_payment_at=None,
    last_attempt_tone: str | None = None,
    has_partial_history: bool = False,
) -> dict:
    """Choose the next single ladder stage using forecast, balance and timing."""
    settings = get_settings()
    today = today or date.today()
    wait_probability = _bounded_probability(probability_14d)
    sent_probability = min(1.0, wait_probability + REMINDER_UPLIFT_ASSUMPTION)
    uplift = sent_probability - wait_probability
    expected_cash_gain = float(Decimal(str(invoice.outstanding)) * Decimal(str(uplift)))
    probabilities = {
        "probability_if_wait": round(wait_probability, 4),
        "probability_if_sent": round(sent_probability, 4),
        "expected_cash_gain": round(expected_cash_gain, 2),
        "expected_cash_gain_currency": getattr(invoice, "currency", None),
        "reminder_uplift_assumption": REMINDER_UPLIFT_ASSUMPTION,
    }

    def result(action: str = "wait", tone: str = "none", reason: str = "", policy_step: int | None = None, **extra) -> dict:
        values = dict(probabilities)
        if action not in {"friendly", "firm", "offer_partial_payment"}:
            values["expected_cash_gain"] = 0.0
        return {"action": action, "tone": tone, "reason": reason, "policy_step": policy_step,
                **values, **extra}

    if invoice.disputed or invoice.status in CLOSED_STATUSES or Decimal(str(invoice.outstanding)) <= 0:
        return result(reason="Invoice is closed, disputed, or has no outstanding balance.")
    if unresolved:
        return result(reason="A previous delivery is unresolved; reconcile it before another reminder.")
    if last_payment_at and (today - last_payment_at.date()).total_seconds() < 48 * 60 * 60:
        return result(reason="A payment arrived within the last 48 hours; allow it to reconcile before another action.")
    if last_attempt_at:
        cooldown = 5 if (last_attempt_tone or "friendly") == "friendly" else 7
        elapsed_days = (today - last_attempt_at.date()).days
        if elapsed_days < cooldown:
            return result(reason=f"A {last_attempt_tone or 'friendly'} reminder was attempted {elapsed_days} days ago; wait {cooldown} days between reminders.")

    days_until_due = (invoice.due_date - today).days
    days_late = max(0, -days_until_due)
    highest_available = _max_stage(days_late, days_until_due)
    current_step = max(0, int(getattr(invoice, "reminder_step", 0) or 0))
    step = min(highest_available, current_step)
    if highest_available >= 0 and step < highest_available:
        # Move only one ladder stage at a time, even if an operator opens the
        # portfolio well after an earlier stage became eligible.
        step = min(highest_available, current_step + 1)

    if step < 0:
        return result(reason="No reminder stage is due yet.")
    if step == 0:
        threshold = settings.friendly_payment_probability_threshold
        if probability_by_due is not None and probability_by_due < threshold:
            return result("friendly", "friendly", f"Friendly pre-due nudge: predicted payment probability by the due date is below {threshold:.0%}.", step)
        return result(reason="Due within three days, but predicted payment probability by the due date is above the reminder threshold.", policy_step=step)
    if step == 1:
        if wait_probability < 0.8:
            return result("friendly", "friendly", "Friendly follow-up at least three days overdue; the 14-day payment forecast is below 80%.", step)
        return result(reason="The 14-day payment forecast is high enough to wait on the +3-day friendly stage.", policy_step=step)
    if step == 2:
        if wait_probability < 0.95:
            return result("firm", "firm", "Firm follow-up at least ten days overdue; the 14-day payment forecast is below 95%.", step)
        return result(reason="The 14-day payment forecast is high enough to wait on the +10-day firm stage.", policy_step=step)
    if step == 3:
        large_balance = Decimal(str(invoice.outstanding)) >= Decimal("2500")
        partial_allowed = bool(getattr(invoice, "partial_payment_allowed", False))
        if large_balance and has_partial_history and not partial_allowed:
            return result("offer_partial_payment", "friendly", "Offer a partial-payment option for this large balance based on the client's prior partial payments.", step)
        if days_late >= 45:
            return result("personal_call", "none", "At least 45 days overdue; contact the client personally and record the outcome.", 4)
        return result(reason="At +21 days, only a large balance with a client partial-payment history qualifies for a partial-payment offer.", policy_step=step)
    return result("personal_call", "none", "At least 45 days overdue; contact the client personally and record the outcome.", 4)


def holdback_assignment(invoice_id: str, decision_date: date, percentage: int = 10) -> bool:
    """Stable 10% exploration holdback for repeatable evaluation and audit."""
    bucket = int(hashlib.sha256(f"{invoice_id}|{decision_date.isoformat()}".encode()).hexdigest()[:8], 16) % 100
    return bucket < percentage


def render_message(client_name: str, balance: Decimal, currency: str, due_date: date, tone: str, payment_url: str | None = None) -> str:
    greeting = f"Hello {client_name},"
    balance_text = f"{currency} {balance:,.2f}"
    link = f"\nPay securely: {payment_url}" if payment_url else ""
    if tone == "firm":
        return f"{greeting}\nOur records show {balance_text} remains outstanding on your invoice due {due_date.isoformat()}. Please arrange payment or contact us if you have questions.{link}\nThank you."
    return f"{greeting}\nA friendly reminder that {balance_text} is due on {due_date.isoformat()}. Please let us know if you need anything.{link}\nThank you."
