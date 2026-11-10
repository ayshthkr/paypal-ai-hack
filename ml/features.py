"""Canonical, point-in-time feature builder shared by training and inference."""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime

FEATURE_SCHEMA_VERSION = "latepayer-features-v2"
GLOBAL_DELAY_PRIOR_DAYS = 10.0
GLOBAL_LATE_RATE_PRIOR = 0.45
GLOBAL_AMOUNT_MEDIAN_PRIOR = 4_000.0
HISTORY_PRIOR_STRENGTH = 3
FEATURES = [
    "log_outstanding", "terms_days", "age_days", "days_until_due", "overdue_days",
    "overdue_1_7", "overdue_8_30", "overdue_31_90", "overdue_91_plus",
    "issue_month_sin", "issue_month_cos", "as_of_month_sin", "as_of_month_cos",
    "weekday_sin", "weekday_cos", "history_count", "history_late_rate", "recent_delay_days",
    "delay_std_days", "history_missing", "prior_paid_invoice_count",
    "amount_vs_client_median_log", "other_open_exposure_log", "client_open_balance_log",
    "viewed_by_recipient", "days_since_viewed", "reminder_count", "days_since_last_reminder",
    "term_due_on_receipt", "term_net_15", "term_net_30", "partial_share",
    "days_since_last_partial_payment", "currency_is_usd", "currency_is_eur", "currency_is_gbp",
]


def _get(record: object, key: str, default=None):
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _amount(value) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def features(invoice: object, as_of: date, history: list[object], all_invoices: list[object] | None = None) -> dict[str, float]:
    issue = _as_date(_get(invoice, "issue_date", as_of)) or as_of
    due = _as_date(_get(invoice, "due_date", issue)) or issue
    age = max(0, (as_of - issue).days)
    settled_history = []
    for item in history:
        item_issue = _as_date(_get(item, "issue_date"))
        settled_at = _as_date(_get(item, "settled_at") or _get(item, "paid_at"))
        item_due = _as_date(_get(item, "due_date")) or item_issue
        if item_issue and item_issue < issue and settled_at and settled_at <= as_of and item_due:
            settled_history.append((item_issue, (settled_at - item_due).days,
                                    _amount(_get(item, "total", _get(item, "paid", 0)))))
    settled_history.sort(key=lambda item: item[0])
    known_delays = [item[1] for item in settled_history]
    known_amounts = [item[2] for item in settled_history]
    recent = known_delays[-5:]
    count = len(known_delays)
    prior_gap = max(0, HISTORY_PRIOR_STRENGTH - count)
    late_rate = (sum(delay > 0 for delay in known_delays) + GLOBAL_LATE_RATE_PRIOR * prior_gap) / max(HISTORY_PRIOR_STRENGTH, count)
    recent_delay = (
        (statistics.median(recent) * len(recent) + GLOBAL_DELAY_PRIOR_DAYS * max(0, HISTORY_PRIOR_STRENGTH - len(recent)))
        / max(HISTORY_PRIOR_STRENGTH, len(recent)) if recent else GLOBAL_DELAY_PRIOR_DAYS
    )
    raw_std = statistics.pstdev(recent) if len(recent) > 1 else 0.0
    delay_std = (raw_std * len(recent) + 6.0 * max(0, HISTORY_PRIOR_STRENGTH - len(recent))) / max(HISTORY_PRIOR_STRENGTH, len(recent))
    raw_amount_median = statistics.median(known_amounts) if known_amounts else GLOBAL_AMOUNT_MEDIAN_PRIOR
    amount_median = (raw_amount_median * count + GLOBAL_AMOUNT_MEDIAN_PRIOR * prior_gap) / max(HISTORY_PRIOR_STRENGTH, count)

    exposure = 0.0
    if all_invoices:
        client_id = _get(invoice, "client_id")
        invoice_id = _get(invoice, "id")
        for item in all_invoices:
            if item is invoice or _get(item, "id") == invoice_id:
                continue
            item_issue = _as_date(_get(item, "issue_date"))
            if _get(item, "client_id") != client_id or not item_issue or item_issue > as_of:
                continue
            settled = _as_date(_get(item, "settled_at") or _get(item, "paid_at"))
            cancelled_at = _as_date(_get(item, "cancelled_at"))
            partial_at = _as_date(_get(item, "partial_at"))
            if (settled and settled <= as_of) or (cancelled_at and cancelled_at <= as_of):
                continue
            amount = _amount(_get(item, "total", _get(item, "outstanding", 0)))
            has_timeline = "outcome_paid_at" in item if isinstance(item, dict) else hasattr(item, "outcome_paid_at")
            if has_timeline:
                if partial_at and partial_at <= as_of:
                    amount -= _amount(_get(item, "partial_amount", _get(item, "paid", 0)))
            elif hasattr(item, "outstanding") or (isinstance(item, dict) and "outstanding" in item):
                amount = _amount(_get(item, "outstanding", amount))
            elif partial_at and partial_at <= as_of:
                amount -= _amount(_get(item, "partial_amount", _get(item, "paid", 0)))
            exposure += max(0.0, amount)

    viewed_at = _as_date(_get(invoice, "viewed_at"))
    observed_at = _as_date(_get(invoice, "viewed_observed_at"))
    viewed = bool(viewed_at and viewed_at <= as_of) or bool(
        _get(invoice, "viewed_by_recipient") and observed_at and observed_at <= as_of
    )
    reminder_days = []
    for event in _get(invoice, "reminder_events", []) or []:
        event_at = _as_date(event.get("sent_at") if isinstance(event, dict) else event)
        if event_at and event_at <= as_of:
            reminder_days.append(event_at)
    partial_at = _as_date(_get(invoice, "partial_at"))
    total = _amount(_get(invoice, "total", 0))
    partial_amount = _amount(_get(invoice, "partial_amount", 0))
    if partial_at and partial_at <= as_of and total > 0:
        partial_share = min(1.0, partial_amount / total)
        days_since_partial = max(0, (as_of - partial_at).days)
    else:
        partial_share = 0.0
        days_since_partial = 180.0

    issue_angle = 2 * math.pi * (issue.month - 1) / 12
    asof_angle = 2 * math.pi * (as_of.month - 1) / 12
    week_angle = 2 * math.pi * as_of.weekday() / 7
    currency = str(_get(invoice, "currency", "USD")).upper()
    term = str(_get(invoice, "payment_term_type", "") or "").upper().replace(" ", "_")
    outstanding = _amount(_get(invoice, "outstanding", _get(invoice, "total", 0)))
    overdue_days = max(0, (as_of - due).days)
    last_viewed_days = max(0, (as_of - viewed_at).days) if viewed_at and viewed_at <= as_of else 180.0
    last_reminder_days = max(0, (as_of - max(reminder_days)).days) if reminder_days else 180.0
    return {
        "log_outstanding": math.log1p(outstanding),
        "terms_days": float(max(0, (due - issue).days)), "age_days": float(age),
        "days_until_due": float((due - as_of).days), "overdue_days": float(overdue_days),
        "overdue_1_7": float(1 <= overdue_days <= 7), "overdue_8_30": float(8 <= overdue_days <= 30),
        "overdue_31_90": float(31 <= overdue_days <= 90), "overdue_91_plus": float(overdue_days > 90),
        "issue_month_sin": math.sin(issue_angle), "issue_month_cos": math.cos(issue_angle),
        "as_of_month_sin": math.sin(asof_angle), "as_of_month_cos": math.cos(asof_angle),
        "weekday_sin": math.sin(week_angle), "weekday_cos": math.cos(week_angle),
        "history_count": float(count), "history_late_rate": late_rate,
        "recent_delay_days": float(recent_delay), "delay_std_days": float(delay_std),
        "history_missing": float(count < HISTORY_PRIOR_STRENGTH), "prior_paid_invoice_count": float(count),
        "amount_vs_client_median_log": math.log1p(_amount(_get(invoice, "total", 0)) / max(1.0, amount_median)),
        "other_open_exposure_log": math.log1p(exposure),
        "client_open_balance_log": math.log1p(exposure + outstanding),
        "viewed_by_recipient": float(viewed), "days_since_viewed": float(last_viewed_days),
        "reminder_count": float(len(reminder_days)), "days_since_last_reminder": float(last_reminder_days),
        "term_due_on_receipt": float(term in {"DUE_ON_RECEIPT", "DUE_ON_RECEIPT"}),
        "term_net_15": float(term in {"NET_15", "NET15"}), "term_net_30": float(term in {"NET_30", "NET30"}),
        "partial_share": partial_share, "days_since_last_partial_payment": float(days_since_partial),
        "currency_is_usd": float(currency == "USD"), "currency_is_eur": float(currency == "EUR"),
        "currency_is_gbp": float(currency == "GBP"),
    }
